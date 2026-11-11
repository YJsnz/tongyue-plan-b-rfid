"""CTC Viterbi word timestamps and conservative known-book position gates.

The PC prototype accepts exact matches or tightly bounded, locally anchored
character edits. Ambiguous and unmatched fragments freeze position; it is not
a pronunciation assessment and never confirms substituted source characters.
"""

import math

MAX_FRAGMENT_STITCH_GAP_SECONDS = 1.5
MAX_FRAGMENT_STITCH_FRAMES = 200


def readable(text):
    return "".join(char for char in text if char.isalnum())


def greedy_ids(scores, blank=0):
    previous, result = None, []
    for token in scores.argmax(axis=1).tolist():
        if token != blank and token != previous:
            result.append(token)
        previous = token
    return result


def forced_align(scores, labels, blank=0, frame_seconds=0.04):
    """Standard CTC max-path trellis, including mandatory blank between repeats.

    Returns token emission intervals, not estimates of phonetic boundaries.
    acousticGap is a path-score gap per frame, never a calibrated confidence.
    """
    import numpy as np
    scores = np.asarray(scores, dtype=np.float64)
    if (scores.ndim != 2 or not 0 < scores.shape[0] <= 3000 or not 0 <= blank < scores.shape[1]
            or not np.isfinite(scores).all() or not labels or len(labels) > 512
            or any(type(item) is not int or item == blank or not 0 <= item < scores.shape[1] for item in labels)
            or not math.isfinite(frame_seconds) or frame_seconds <= 0):
        raise ValueError("Invalid or excessive CTC alignment input")
    count = len(labels) * 2 + 1
    if len(scores) * count > 3000000:
        raise ValueError("CTC alignment exceeds the PC trellis limit")
    # Normalize logits as well as log probabilities, so gaps share one scale.
    shifted = scores - scores.max(axis=1, keepdims=True)
    scores = shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))
    states = np.full(count, blank, dtype=np.int32)
    states[1::2] = labels
    skip = np.zeros(count, dtype=bool)
    skip[2:] = (states[2:] != blank) & (states[2:] != states[:-2])
    previous = np.full(count, -np.inf)
    previous[0] = scores[0, blank]
    previous[1] = scores[0, labels[0]]
    history = np.zeros((len(scores), count), dtype=np.uint8)
    for frame in range(1, len(scores)):
        advance = np.concatenate(([-np.inf], previous[:-1]))
        jump = np.concatenate(([-np.inf, -np.inf], previous[:-2]))
        jump[~skip] = -np.inf
        choices = np.stack((previous, advance, jump))
        movement = choices.argmax(axis=0)
        history[frame] = movement
        previous = choices[movement, np.arange(count)] + scores[frame, states]
    state = count - 1 if previous[-1] > previous[-2] else count - 2
    total = previous[state]
    if not np.isfinite(total):
        raise ValueError("Audio has too few CTC frames for this reference")
    path = np.zeros(len(scores), dtype=np.int32)
    for frame in range(len(scores) - 1, -1, -1):
        path[frame] = state
        state -= int(history[frame, state])
    intervals = []
    for index, label in enumerate(labels):
        frames = np.flatnonzero(path == index * 2 + 1)
        if not len(frames):
            raise ValueError("Incomplete CTC path")
        intervals.append({"startSeconds": float(frames[0] * frame_seconds),
                          "endSeconds": float((frames[-1] + 1) * frame_seconds),
                          "meanPosterior": float(np.exp(scores[frames, label]).mean())})
    return {"acousticGap": float((scores.max(axis=1).sum() - total) / len(scores)),
            "tokens": intervals}


def _local_edit_alignments(query, reference):
    """Align a short decoded fragment to any substring using character edit DP."""
    query_size, reference_size = len(query), len(reference)
    if not query_size or query_size > 128 or not reference_size or reference_size > 2048:
        return []
    previous = [0] * (reference_size + 1)  # The reference may begin before this local window.
    history = []
    for row_index, char in enumerate(query, 1):
        current = [row_index] + [0] * reference_size
        moves = bytearray(reference_size + 1)
        moves[0] = 1
        for column in range(1, reference_size + 1):
            diagonal = previous[column - 1] + (char != reference[column - 1])
            inserted = previous[column] + 1
            deleted = current[column - 1] + 1
            best = min(diagonal, inserted, deleted)
            current[column] = best
            moves[column] = 0 if diagonal == best else 1 if inserted == best else 2
        history.append(moves)
        previous = current
    distance = min(previous[1:])
    alignments, seen = [], set()
    for endpoint, score in enumerate(previous[1:], 1):
        if score != distance:
            continue
        query_to_reference = [None] * query_size
        row, column = query_size, endpoint
        while row:
            move = history[row - 1][column]
            if move == 0:
                if query[row - 1] == reference[column - 1]:
                    query_to_reference[row - 1] = column - 1
                row -= 1
                column -= 1
            elif move == 1:
                row -= 1
            else:
                column -= 1
        key = (column, tuple(query_to_reference))
        if key not in seen:
            seen.add(key)
            alignments.append((distance, column, query_to_reference))
            if len(alignments) == 2:
                break  # Two equally good placements are enough to reject ambiguity.
    return alignments


def event(backend, status, segment_id, start, end, *, words=None, reason=None, **details):
    """Shared JSONL contract: no invented word positions for segment-only MFCC."""
    return {"formatVersion": 1, "backend": backend,
            "granularity": "word" if backend == "ctc" else "segment",
            "status": status, "segmentId": segment_id,
            "startSeconds": start, "endSeconds": end, "words": words or [],
            "reason": reason, **details}


class CtcBookTracker:
    def __init__(self, text_index, acoustic):
        self.index, self.acoustic = text_index, acoustic
        self.position = None
        self.final_position = None
        self.pending_fragment = None
        self.references = []
        for segment_index, record in enumerate(text_index.segments):
            offsets = [index for index, char in enumerate(record["text"]) if char.isalnum()]
            normalized = readable(record["text"])
            # Check vocabulary up front, rather than producing partial results for
            # a book whose later chapters cannot be represented by this model.
            acoustic.encode_text(normalized)
            self.references.append((segment_index, record, normalized, offsets))

    def _stitch_pending(self, scores, start_seconds):
        if self.pending_fragment is None or not len(scores):
            return None
        import numpy as np
        previous_scores, previous_start = self.pending_fragment
        frame_seconds = self.acoustic.frame_seconds
        previous_end = previous_start + len(previous_scores) * frame_seconds
        gap_seconds = start_seconds - previous_end
        if gap_seconds < -frame_seconds or gap_seconds > MAX_FRAGMENT_STITCH_GAP_SECONDS:
            self.pending_fragment = None
            return None
        gap_frames = max(0, round(max(0, gap_seconds) / frame_seconds))
        total_frames = len(previous_scores) + gap_frames + len(scores)
        if total_frames > MAX_FRAGMENT_STITCH_FRAMES:
            self.pending_fragment = None
            return None
        if gap_frames:
            silence = np.full((gap_frames, scores.shape[1]), -50.0, dtype=scores.dtype)
            silence[:, self.acoustic.blank_id] = 0.0
            scores = np.concatenate((previous_scores, silence, scores), axis=0)
        else:
            scores = np.concatenate((previous_scores, scores), axis=0)
        return scores, previous_start

    def locate(self, scores, start_seconds=0, *, final=True, end_seconds=None):
        stitched = self._stitch_pending(scores, start_seconds)
        if stitched is not None:
            combined, combined_start = stitched
            if not final:
                return self._locate_once(combined, combined_start, final=False, end_seconds=end_seconds)
            combined_text = readable(self.acoustic.decode_ids(
                greedy_ids(combined, self.acoustic.blank_id)))
            if len(combined_text) < 4:
                self.pending_fragment = (combined.copy(), combined_start)
                return self._locate_once(combined, combined_start, final=True, end_seconds=end_seconds)
            result = self._locate_once(combined, combined_start, final=True, end_seconds=end_seconds)
            self.pending_fragment = None
            if result["status"] != "uncertain":
                return result
            # A short unrelated fragment must not prevent a clean new utterance
            # from matching on its own.
            result = self._locate_once(scores, start_seconds, final=True, end_seconds=end_seconds)
            if result.get("reason") == "insufficient_context" and len(scores) <= MAX_FRAGMENT_STITCH_FRAMES:
                self.pending_fragment = (scores.copy(), start_seconds)
            return result

        result = self._locate_once(scores, start_seconds, final=final, end_seconds=end_seconds)
        if (final and result.get("reason") == "insufficient_context" and
                len(scores) <= MAX_FRAGMENT_STITCH_FRAMES):
            self.pending_fragment = (scores.copy(), start_seconds)
        return result

    def _locate_once(self, scores, start_seconds=0, *, final=True, end_seconds=None):
        end = start_seconds + len(scores) * self.acoustic.frame_seconds
        end = end if end_seconds is None else min(end, end_seconds)
        old_segment = self.position[0] if self.position else None

        def uncertain(reason, **extra):
            return event("ctc", "uncertain", old_segment, start_seconds, end,
                         reason=reason, final=final,
                         wordIndex=self.position[1] if self.position else None, **extra)

        if not len(scores):
            return uncertain("silence")
        decoded = self.acoustic.decode_ids(greedy_ids(scores, self.acoustic.blank_id))
        if "�" in decoded:
            return uncertain("incomplete_token")
        hypothesis = readable(decoded)
        if not hypothesis:
            return uncertain("silence")
        if len(hypothesis) < 4:
            return uncertain("insufficient_context")
        anchor = self.final_position or self.position
        anchor_index = None
        if anchor:
            anchor_index = next((index for index, record, _, _ in self.references
                                 if record["segmentId"] == anchor[0]), None)
        local_start = max(0, anchor_index - 1) if anchor_index is not None else 0
        local_end = min(len(self.references), anchor_index + 3) if anchor_index is not None else 0

        def exact_matches(indices=None):
            found = []
            for segment_index, record, normalized, offsets in self.references:
                if indices is not None and not local_start <= segment_index < local_end:
                    continue
                start = normalized.find(hypothesis)
                while start >= 0:
                    mapping = list(range(start, start + len(hypothesis)))
                    found.append((segment_index, record, start, start + len(hypothesis), offsets, mapping, 0))
                    if len(found) > 1:
                        return found
                    start = normalized.find(hypothesis, start + 1)
            return found

        candidates = exact_matches(indices=True) if anchor_index is not None else []
        if len(candidates) > 1:
            return uncertain("ambiguous_reference")
        if not candidates:
            candidates = exact_matches()
            if len(candidates) > 1:
                return uncertain("ambiguous_reference")
        if not candidates and anchor_index is not None and 8 <= len(hypothesis) <= 128:
            max_edits = min(2, max(1, len(hypothesis) // 12))
            minimum_exact = max(6, math.ceil(len(hypothesis) * 0.8))
            fuzzy, local_costs, ambiguous_placement = [], [], False
            for segment_index, record, normalized, offsets in self.references[local_start:local_end]:
                alignments = _local_edit_alignments(hypothesis, normalized)
                if not alignments:
                    continue
                distance = alignments[0][0]
                local_costs.append((segment_index, distance))
                if distance > max_edits:
                    continue
                if len(alignments) > 1:
                    ambiguous_placement = True
                for _, begin, mapping in alignments:
                    exact_count = sum(index is not None for index in mapping)
                    if exact_count < minimum_exact:
                        continue
                    finish = max(index for index in mapping if index is not None) + 1
                    fuzzy.append((segment_index, record, begin, finish, offsets, mapping, distance))
            if fuzzy:
                best_distance = min(candidate[-1] for candidate in fuzzy)
                best = [candidate for candidate in fuzzy if candidate[-1] == best_distance]
                second_distance = min((distance for index, distance in local_costs
                                       if index != best[0][0]), default=None)
                if (ambiguous_placement or len(best) != 1 or
                        (second_distance is not None and second_distance - best_distance < 2)):
                    return uncertain("ambiguous_local_alignment")
                candidates = best
            elif ambiguous_placement:
                return uncertain("ambiguous_local_alignment")
        if not candidates:
            return uncertain("reference_mismatch")
        try:
            if hasattr(self.acoustic, "alignment_tokens"):
                labels, character_spans = self.acoustic.alignment_tokens(hypothesis)
            else:
                labels = self.acoustic.encode_text(hypothesis)
                character_spans = [(index, index + 1) for index in range(len(hypothesis))]
            aligned = forced_align(scores, labels,
                                   self.acoustic.blank_id, self.acoustic.frame_seconds)
        except ValueError:
            return uncertain("unalignable_reference")
        # Heuristic research gates, to be calibrated with authorized child speech.
        if aligned["acousticGap"] > 0.3 or min(item["meanPosterior"] for item in aligned["tokens"]) < 0.05:
            return uncertain("weak_acoustics", acousticGap=aligned["acousticGap"])
        segment_index, record, begin, finish, offsets, mapping, edit_distance = candidates[0]
        character_times = []
        for begin_token, end_token in character_spans:
            tokens = aligned["tokens"][begin_token:end_token]
            character_times.append({"startSeconds": tokens[0]["startSeconds"],
                                    "endSeconds": tokens[-1]["endSeconds"],
                                    "meanPosterior": sum(token["meanPosterior"] for token in tokens) / len(tokens)})
        by_offset = {offsets[reference_index]: character_times[query_index]
                     for query_index, reference_index in enumerate(mapping) if reference_index is not None}
        covered = sorted(by_offset)
        if not covered:
            return uncertain("incomplete_word")
        words = []
        for word_index, (word_start, word_end, _) in enumerate(record["words"]):
            indices = [offset for offset in range(word_start, word_end) if record["text"][offset].isalnum()]
            if not indices or not all(offset in by_offset for offset in indices):
                continue
            first, last = by_offset[indices[0]], by_offset[indices[-1]]
            word_end_seconds = start_seconds + last["endSeconds"]
            if word_end_seconds > end + 0.000001:
                continue  # Encoder tail padding is not an observed source word.
            words.append({"wordIndex": word_index, "textStart": word_start, "textEnd": word_end,
                          "text": record["text"][word_start:word_end],
                          "startSeconds": start_seconds + first["startSeconds"],
                          "endSeconds": word_end_seconds,
                          "meanPosterior": sum(by_offset[offset]["meanPosterior"] for offset in indices) / len(indices)})
        if not words:
            return uncertain("incomplete_word")
        next_position = (record["segmentId"], words[-1]["wordIndex"])
        status = "matched"
        if self.final_position:
            final_segment, final_word = self.final_position
            previous_segment = next(index for index, item, _, _ in self.references if item["segmentId"] == final_segment)
            if next_position[0] == final_segment and next_position[1] <= final_word:
                status = "repeated"
            elif segment_index not in (previous_segment, previous_segment + 1):
                status = "relocated"
            elif segment_index == previous_segment and words[0]["wordIndex"] > final_word + 1:
                status = "relocated"
        self.position = next_position
        if final:
            self.final_position = next_position
        return event("ctc", status, record["segmentId"], start_seconds, end, words=words,
                     wordIndex=next_position[1], textStart=covered[0], textEnd=covered[-1] + 1,
                     acousticGap=aligned["acousticGap"], final=final,
                     alignmentMode="local_edit" if edit_distance else "exact", editDistance=edit_distance)
