"""CTC Viterbi word timestamps and conservative known-book position gates.

The PC prototype accepts exact matches or tightly bounded, locally anchored
character edits. Ambiguous and unmatched fragments freeze position; it is not
a pronunciation assessment and never confirms substituted source characters.
"""

import math
import re

MAX_FRAGMENT_STITCH_GAP_SECONDS = 1.5
MAX_FRAGMENT_STITCH_FRAMES = 200
MIN_BOOK_DP_CHARS = 16
MAX_BOOK_DP_CANDIDATES = 16
SYLLABLE_BEAM_WIDTH = 32
SYLLABLE_BEAM_RADIUS = 40
MIN_SYLLABLE_BEAM_HITS = 4
MIN_SYLLABLE_BEAM_MARGIN = 0.75
BLANK_FREEZE_PROBABILITY = 0.9


def readable(text):
    return "".join(char for char in text if char.isalnum())


def toneless(syllable):
    return re.sub(r"[1-5]$", "", syllable)


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


def _fuzzy_candidates(hypothesis, references, max_edits, minimum_exact):
    fuzzy, costs, ambiguous = [], [], False
    for segment_index, record, normalized, offsets in references:
        alignments = _local_edit_alignments(hypothesis, normalized)
        if not alignments:
            continue
        distance = alignments[0][0]
        costs.append((segment_index, distance))
        if distance > max_edits:
            continue
        if len(alignments) > 1:
            ambiguous = True
        for _, begin, mapping in alignments:
            exact_count = sum(index is not None for index in mapping)
            if exact_count < minimum_exact:
                continue
            finish = max(index for index in mapping if index is not None) + 1
            fuzzy.append((segment_index, record, begin, finish, offsets, mapping, distance))
    return fuzzy, costs, ambiguous


def _resolve_fuzzy(fuzzy, costs, ambiguous):
    if not fuzzy:
        return None, ambiguous
    best_distance = min(candidate[-1] for candidate in fuzzy)
    best = [candidate for candidate in fuzzy if candidate[-1] == best_distance]
    other_distances = [distance for index, distance in costs if index != best[0][0]]
    second_distance = min(other_distances, default=None)
    if (ambiguous or len(best) != 1 or
            (second_distance is not None and second_distance - best_distance < 2)):
        return None, True
    return best[0], False


def _book_dp_seeded_references(hypothesis, references, local_start, local_end):
    grams = {hypothesis[index:index + 3] for index in range(len(hypothesis) - 2)}
    if len(grams) < 2:
        return []
    candidates = []
    for reference in references:
        segment_index, _, normalized, _ = reference
        if (local_start <= segment_index < local_end or not 4 <= len(normalized) <= 2048 or
                sum(1 for gram in grams if gram in normalized) < 2):
            continue
        candidates.append(reference)
        if len(candidates) > MAX_BOOK_DP_CANDIDATES:
            return None
    return candidates


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
        self.syllable_nodes = []
        self.syllable_words = {}
        self.syllable_label_ids = self._syllable_label_map(acoustic)
        self.syllable_beam_state = None
        for segment_index, record in enumerate(text_index.segments):
            offsets = [index for index, char in enumerate(record["text"]) if char.isalnum()]
            normalized = readable(record["text"])
            # Check vocabulary up front, rather than producing partial results for
            # a book whose later chapters cannot be represented by this model.
            acoustic.encode_text(normalized)
            self.references.append((segment_index, record, normalized, offsets))
            if hasattr(text_index, "tokens"):
                for token in text_index.tokens(record["segmentId"]):
                    pinyin = toneless(token["pinyin"])
                    text_start, text_end = token["textStart"], token["textEnd"]
                    source_char = record["text"][text_start:text_end]
                    token_ids = getattr(acoustic, "token_ids", getattr(acoustic, "ids", {}))
                    node_index = len(self.syllable_nodes)
                    key = (segment_index, token["wordIndex"])
                    node = {"segmentIndex": segment_index, "segmentId": record["segmentId"],
                            "wordIndex": token["wordIndex"], "textStart": text_start, "textEnd": text_end,
                            "pinyin": pinyin, "labels": self.syllable_label_ids.get(pinyin, []),
                            "sourceLabel": token_ids.get(source_char) if len(source_char) == 1 else None,
                            "wordStart": key not in self.syllable_words}
                    self.syllable_nodes.append(node)
                    self.syllable_words.setdefault(key, []).append(node_index)

    @staticmethod
    def _syllable_label_map(acoustic):
        supplied = getattr(acoustic, "syllable_label_ids", None)
        if isinstance(supplied, dict):
            return {key: list(value) for key, value in supplied.items() if isinstance(value, (list, tuple))}
        tokens = getattr(acoustic, "tokens", None)
        if not isinstance(tokens, dict):
            return {}
        try:
            from pypinyin import Style, pinyin
        except ImportError:
            return {}
        labels = {}
        for label_id, token in tokens.items():
            if (not isinstance(token, str) or len(token) != 1 or
                    not ("\u3400" <= token <= "\u9fff" or "\uf900" <= token <= "\ufaff")):
                continue
            for choices in pinyin(token, style=Style.TONE3, heteronym=True,
                                  neutral_tone_with_five=True, errors=lambda value: [value]):
                for choice in choices:
                    if re.fullmatch(r"[a-zê]+[1-5]", choice):
                        labels.setdefault(toneless(choice), set()).add(label_id)
        return {key: sorted(value) for key, value in labels.items()}

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

    def _syllable_beam(self, scores, start_seconds, *, final=True, end_seconds=None):
        if not self.syllable_nodes or not self.syllable_label_ids:
            return None
        import numpy as np
        beam_state = self.syllable_beam_state
        if beam_state is not None:
            processed = beam_state["processed"]
            stale = (beam_state["startSeconds"] != start_seconds or len(scores) < processed or
                     scores.shape[1] != beam_state["vocabularySize"] or
                     (processed and not np.array_equal(scores[processed - 1], beam_state["lastFrame"])))
            if stale:
                beam_state = None
                self.syllable_beam_state = None
        if len(scores) < MIN_SYLLABLE_BEAM_HITS:
            return None

        if beam_state is None:
            anchor = self.final_position or self.position
            if anchor is None:
                return None
            anchor_node = max((index for index, node in enumerate(self.syllable_nodes)
                               if node["segmentId"] == anchor[0] and node["wordIndex"] == anchor[1]), default=None)
            if anchor_node is None:
                return None
            left = max(0, anchor_node - SYLLABLE_BEAM_RADIUS)
            right = min(len(self.syllable_nodes), anchor_node + SYLLABLE_BEAM_RADIUS + 1)
            window = self.syllable_nodes[left:right]
            width = len(window)
            start_states = [index for index, node in enumerate(window)
                            if node["wordStart"] and left + index <= anchor_node]
            if anchor_node + 1 < right:
                start_states.append(anchor_node + 1 - left)
            start_states = sorted(set(start_states))
            if not start_states or width < MIN_SYLLABLE_BEAM_HITS:
                return None
            beam_state = {"startSeconds": start_seconds, "anchor": anchor,
                          "left": left, "window": window, "width": width,
                          "startStates": start_states, "processed": 0,
                          "previous": np.full(width, -np.inf, dtype=np.float64), "history": [],
                          "blankFrames": [], "blankRun": 0, "lastFrame": None,
                          "vocabularySize": scores.shape[1]}
            self.syllable_beam_state = beam_state
        anchor = beam_state["anchor"]
        left = beam_state["left"]
        window, width = beam_state["window"], beam_state["width"]
        frame_seconds = self.acoustic.frame_seconds
        end = start_seconds + len(scores) * frame_seconds
        end = end if end_seconds is None else min(end, end_seconds)

        def prune(values):
            active = np.flatnonzero(np.isfinite(values))
            if len(active) > SYLLABLE_BEAM_WIDTH:
                keep = active[np.argpartition(values[active], -SYLLABLE_BEAM_WIDTH)[-SYLLABLE_BEAM_WIDTH:]]
                removed = np.ones(len(values), dtype=bool)
                removed[keep] = False
                values[removed] = -np.inf
            return values

        while beam_state["processed"] < len(scores):
            frame = beam_state["processed"]
            frame_scores = scores[frame]
            blank_probability = float(np.exp(np.clip(frame_scores[self.acoustic.blank_id], -60, 0)))
            blank_frame = blank_probability >= BLANK_FREEZE_PROBABILITY
            emissions = np.full(width, -12.0, dtype=np.float32)
            for index, node in enumerate(window):
                labels = node["labels"]
                if labels:
                    emissions[index] = np.max(frame_scores[labels])
            history_row = np.full(width, -2, dtype=np.int16)
            if frame == 0:
                previous = beam_state["previous"]
                first_emission = np.where(blank_frame, 0.0, emissions)
                previous[beam_state["startStates"]] = first_emission[beam_state["startStates"]]
                previous = prune(previous)
                history_row[beam_state["startStates"]] = -1
                blank_run = 1 if blank_frame else 0
            else:
                blank_run = beam_state["blankRun"] + 1 if blank_frame else 0
                previous = beam_state["previous"]
                current = np.full(width, -np.inf, dtype=np.float64)
                for target in range(width):
                    options = [(target, -0.2)]
                    if blank_run >= 2:
                        pass  # Sustained silence freezes the beam position.
                    elif blank_run == 1:
                        if (target > 0 and window[target - 1]["pinyin"] == window[target]["pinyin"]):
                            options.append((target - 1, -1.5))
                    else:
                        if target > 0:
                            options.append((target - 1, -0.45))
                        if target > 1:
                            options.append((target - 2, -2.5))
                        if target + 1 < width:
                            options.append((target + 1, -2.5))
                    best_value, best_source = -np.inf, -1
                    for source, penalty in options:
                        value = previous[source] + penalty
                        if value > best_value:
                            best_value, best_source = value, source
                    if best_source >= 0:
                        current[target] = best_value + (0.0 if blank_frame else emissions[target])
                        history_row[target] = best_source
                previous = prune(current)
            beam_state["previous"] = previous
            beam_state["blankRun"] = blank_run
            beam_state["history"].append(history_row)
            beam_state["blankFrames"].append(blank_frame)
            beam_state["processed"] = frame + 1
            beam_state["lastFrame"] = frame_scores.copy()
            if not np.isfinite(previous).any():
                self.syllable_beam_state = None
                return None

        previous = beam_state["previous"]
        blank_frames = beam_state["blankFrames"]
        scores_by_word = {}
        for index, value in enumerate(previous):
            if not np.isfinite(value):
                continue
            node = window[index]
            key = (node["segmentIndex"], node["wordIndex"])
            word_end = self.syllable_words[key][-1] - left
            if index == word_end:
                scores_by_word[key] = float(value)
        ranked = sorted(scores_by_word.items(), key=lambda item: item[1], reverse=True)
        if not ranked:
            return None
        best_key, best_score = ranked[0]
        margin = best_score - ranked[1][1] if len(ranked) > 1 else 0.0
        local_end = self.syllable_words[best_key][-1] - left
        if not 0 <= local_end < width or not np.isfinite(previous[local_end]):
            return None
        path = np.full(len(scores), -1, dtype=np.int16)
        path_state = local_end
        for frame in range(len(scores) - 1, -1, -1):
            if path_state < 0:
                break
            path[frame] = path_state
            path_state = int(beam_state["history"][frame][path_state])

        observed = set()
        exact_frames = {}
        for frame, local_index in enumerate(path):
            if local_index < 0 or blank_frames[frame]:
                continue
            node = window[int(local_index)]
            labels = node["labels"]
            if not labels or np.max(scores[frame, labels]) < math.log(0.05):
                continue
            node_index = left + int(local_index)
            observed.add(node_index)
            source_label = node["sourceLabel"]
            if (source_label is not None and source_label in labels and
                    labels[int(np.argmax(scores[frame, labels]))] == source_label and
                    scores[frame, source_label] >= math.log(0.05)):
                exact_frames.setdefault(node_index, []).append(frame)
        if len(observed) < MIN_SYLLABLE_BEAM_HITS:
            return None
        if margin < MIN_SYLLABLE_BEAM_MARGIN:
            return event("ctc", "uncertain", anchor[0], start_seconds, end,
                         reason="ambiguous_syllable_alignment", final=final,
                         wordIndex=anchor[1], syllableBeamGap=margin)

        segment_index = best_key[0]
        record = self.references[segment_index][1]
        words = []
        for (word_segment, word_index), node_indices in sorted(self.syllable_words.items()):
            if (word_segment != segment_index or word_index > best_key[1] or
                    any(index not in observed or index not in exact_frames for index in node_indices)):
                continue
            start_frame = min(frame for index in node_indices for frame in exact_frames[index])
            end_frame = max(frame for index in node_indices for frame in exact_frames[index])
            text_start, text_end, _ = record["words"][word_index]
            word_start_seconds = start_seconds + start_frame * frame_seconds
            word_end_seconds = start_seconds + (end_frame + 1) * frame_seconds
            if word_end_seconds > end + 0.000001:
                continue
            words.append({"wordIndex": word_index, "textStart": text_start, "textEnd": text_end,
                          "text": record["text"][text_start:text_end],
                          "startSeconds": word_start_seconds, "endSeconds": word_end_seconds,
                          "meanPosterior": float(np.mean([
                              np.exp(scores[frame, self.syllable_nodes[index]["sourceLabel"]])
                              for index in node_indices for frame in exact_frames[index]]))})
        if not words:
            return event("ctc", "uncertain", anchor[0], start_seconds, end,
                         reason="incomplete_syllable_word", final=final,
                         wordIndex=anchor[1], syllableBeamGap=margin,
                         candidateSegmentId=record["segmentId"], candidateWordIndex=best_key[1],
                         observedSyllables=len(observed), exactSyllables=len(exact_frames))
        next_position = (record["segmentId"], words[-1]["wordIndex"])
        status = "matched"
        if self.final_position:
            final_segment, final_word = self.final_position
            previous_segment = next(index for index, item, _, _ in self.references
                                    if item["segmentId"] == final_segment)
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
                     wordIndex=next_position[1], textStart=words[0]["textStart"],
                     textEnd=words[-1]["textEnd"], final=final,
                     alignmentMode="syllable_beam", syllableBeamGap=margin,
                     observedSyllables=len(observed))

    def locate(self, scores, start_seconds=0, *, final=True, end_seconds=None):
        try:
            return self._locate_with_fragments(scores, start_seconds, final=final, end_seconds=end_seconds)
        finally:
            if final:
                self.syllable_beam_state = None

    def _locate_with_fragments(self, scores, start_seconds=0, *, final=True, end_seconds=None):
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
        alignment_mode = "exact"

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
            fuzzy, costs, ambiguous = _fuzzy_candidates(
                hypothesis, self.references[local_start:local_end], max_edits, minimum_exact)
            selected, ambiguous = _resolve_fuzzy(fuzzy, costs, ambiguous)
            if selected is not None:
                candidates = [selected]
                alignment_mode = "local_edit"
            elif ambiguous:
                return uncertain("ambiguous_local_alignment")
        if (not candidates and anchor_index is not None and
                MIN_BOOK_DP_CHARS <= len(hypothesis) <= 128):
            max_edits = min(2, max(1, len(hypothesis) // 12))
            minimum_exact = max(6, math.ceil(len(hypothesis) * 0.8))
            references = _book_dp_seeded_references(
                hypothesis, self.references, local_start, local_end)
            if references is None:
                return uncertain("ambiguous_book_search")
            fuzzy, costs, ambiguous = _fuzzy_candidates(hypothesis, references, max_edits, minimum_exact)
            selected, ambiguous = _resolve_fuzzy(fuzzy, costs, ambiguous)
            if selected is not None:
                candidates = [selected]
                alignment_mode = "book_dp"
            elif ambiguous:
                return uncertain("ambiguous_book_alignment")
        if not candidates:
            aligned = self._syllable_beam(scores, start_seconds, final=final, end_seconds=end_seconds)
            if aligned is not None:
                return aligned
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
                     alignmentMode=alignment_mode if edit_distance else "exact", editDistance=edit_distance)
