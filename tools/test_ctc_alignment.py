"""CTC path correctness and fixed reading-anomaly gates, without a model download."""

import itertools
import math
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from ctc_alignment import CtcBookTracker, forced_align, greedy_ids
from ctc_acoustic import ZipformerCtc
from reading_probe import ctc_events


class FakeAcoustic:
    blank_id, frame_seconds = 0, 0.04

    def __init__(self, texts):
        self.tokens = {0: ""}
        self.tokens.update({index: char for index, char in enumerate(sorted(set("".join(texts))), 1)})
        self.ids = {value: key for key, value in self.tokens.items()}

    def encode_text(self, text):
        return [self.ids[char] for char in text]

    def decode_ids(self, ids):
        return "".join(self.tokens[item] for item in ids)

    def scores(self, text):
        labels = [0]
        for item in self.encode_text(text):
            labels.extend((item, item, 0))
        labels.extend([0] * 20)
        values = np.full((len(labels), len(self.tokens)), 0.001)
        values[np.arange(len(labels)), labels] = 0.99
        values /= values.sum(axis=1, keepdims=True)
        return np.log(values)


class FakeIndex:
    def __init__(self, texts):
        self.segments = [{"segmentId": f"s{index + 1}", "text": text,
                          "words": [(start, min(start + 2, len(text)), 0) for start in range(0, len(text), 2)]}
                         for index, text in enumerate(texts)]


@unittest.skipIf(np is None, "Optional PC CTC requirements are not installed")
class CtcAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.texts = ["小熊带着雨伞出门", "小兔站在大树下面", "大家一起高兴回家"]
        self.acoustic = FakeAcoustic(self.texts + ["今天午饭吃什么"])
        self.tracker = CtcBookTracker(FakeIndex(self.texts), self.acoustic)

    def locate(self, text, **kwargs):
        return self.tracker.locate(self.acoustic.scores(text), **kwargs)

    def test_ctc_path_matches_exhaustive_search_and_requires_repeat_blank(self):
        rng = np.random.default_rng(2026)
        values = rng.random((5, 3)) + 0.1
        scores = np.log(values / values.sum(axis=1, keepdims=True))
        for target in ([1], [1, 1], [1, 2], [2, 1, 2]):
            paths = [path for path in itertools.product(range(3), repeat=5)
                     if [value for index, value in enumerate(path) if value and (not index or value != path[index - 1])] == target]
            best = max(sum(scores[index, value] for index, value in enumerate(path)) for path in paths)
            result = forced_align(scores, target)
            self.assertAlmostEqual(result["acousticGap"], (scores.max(axis=1).sum() - best) / len(scores))
        with self.assertRaisesRegex(ValueError, "too few"):
            forced_align(np.log([[0.1, 0.9], [0.1, 0.9]]), [1, 1])

    def test_word_times_offsets_and_partial_final_are_not_false_repeats(self):
        partial = self.locate(self.texts[0][:4], final=False)
        self.assertEqual((partial["segmentId"], partial["wordIndex"]), ("s1", 1))
        self.assertEqual(partial["words"][0]["text"], "小熊")
        final = self.locate(self.texts[0])
        self.assertEqual(final["status"], "matched")
        self.assertEqual(final["wordIndex"], 3)
        self.assertTrue(all(word["startSeconds"] < word["endSeconds"] for word in final["words"]))
        self.assertEqual([(word["textStart"], word["textEnd"]) for word in final["words"]], [(0, 2), (2, 4), (4, 6), (6, 8)])

    def test_short_fragments_stitch_across_a_brief_pause_and_recover_the_whole_phrase(self):
        first_text, second_text = self.texts[0][:3], self.texts[0][3:]
        first_scores = self.acoustic.scores(first_text)
        first = self.tracker.locate(first_scores, start_seconds=1.0)
        self.assertEqual((first["status"], first["reason"]), ("uncertain", "insufficient_context"))
        first_end = 1.0 + len(first_scores) * self.acoustic.frame_seconds

        result = self.tracker.locate(self.acoustic.scores(second_text), start_seconds=first_end + 0.8)
        self.assertEqual((result["status"], result["segmentId"], result["wordIndex"]), ("matched", "s1", 3))
        self.assertEqual([word["text"] for word in result["words"]], ["小熊", "带着", "雨伞", "出门"])
        self.assertEqual(result["startSeconds"], 1.0)
        self.assertAlmostEqual(result["endSeconds"], first_end + 0.8 +
                               len(self.acoustic.scores(second_text)) * self.acoustic.frame_seconds)
        self.assertIsNone(self.tracker.pending_fragment)

    def test_stitch_buffer_expires_after_a_long_pause(self):
        first_scores = self.acoustic.scores(self.texts[0][:3])
        self.tracker.locate(first_scores, start_seconds=0)
        later_start = len(first_scores) * self.acoustic.frame_seconds + 1.6
        result = self.tracker.locate(self.acoustic.scores(self.texts[1]), start_seconds=later_start)
        self.assertEqual((result["status"], result["segmentId"], result["alignmentMode"]),
                         ("matched", "s2", "exact"))
        self.assertEqual(result["startSeconds"], later_start)
        self.assertIsNone(self.tracker.pending_fragment)

    def test_unrelated_short_fragment_falls_back_to_a_clean_exact_utterance(self):
        foreign_scores = self.acoustic.scores("今天午")
        self.tracker.locate(foreign_scores, start_seconds=0)
        next_start = len(foreign_scores) * self.acoustic.frame_seconds + 0.4
        result = self.tracker.locate(self.acoustic.scores(self.texts[0]), start_seconds=next_start)
        self.assertEqual((result["status"], result["segmentId"], result["alignmentMode"]),
                         ("matched", "s1", "exact"))
        self.assertEqual(result["startSeconds"], next_start)

    def test_anomaly_repeat_retains_observed_word(self):
        self.locate(self.texts[0])
        result = self.locate(self.texts[0][:4])
        self.assertEqual((result["status"], result["segmentId"], result["wordIndex"]), ("repeated", "s1", 1))

    def test_anomaly_forward_skip_relocates_with_exact_context(self):
        self.locate(self.texts[0])
        result = self.locate(self.texts[2])
        self.assertEqual((result["status"], result["segmentId"]), ("relocated", "s3"))

    def test_anomaly_backward_jump_relocates(self):
        self.locate(self.texts[2])
        result = self.locate(self.texts[0])
        self.assertEqual((result["status"], result["segmentId"]), ("relocated", "s1"))

    def test_anomaly_misread_freezes_and_next_clean_fragment_recovers(self):
        self.locate(self.texts[0])
        result = self.locate("小兔站在雨伞下面")
        self.assertEqual((result["status"], result["segmentId"], result["reason"]),
                         ("uncertain", "s1", "reference_mismatch"))
        self.assertEqual(self.locate(self.texts[1])["segmentId"], "s2")

    def test_local_dp_aligns_one_inserted_character_without_inventing_source_text(self):
        self.locate(self.texts[0])
        result = self.locate("小熊带着大雨伞出门")
        self.assertEqual((result["status"], result["segmentId"], result["alignmentMode"]),
                         ("repeated", "s1", "local_edit"))
        self.assertEqual([word["text"] for word in result["words"]], ["小熊", "带着", "雨伞", "出门"])
        self.assertEqual(result["editDistance"], 1)
        from types import SimpleNamespace
        from reading_trace import validated_alignment
        self.assertEqual(validated_alignment(SimpleNamespace(text_index=lambda: self.tracker.index), result),
                         result["words"][-1])

    def test_local_dp_does_not_guess_initial_position_without_context(self):
        result = self.locate("小熊带着大雨伞出门")
        self.assertEqual((result["status"], result["reason"]), ("uncertain", "reference_mismatch"))
        self.assertIsNone(self.tracker.position)

    def test_book_dp_relocates_a_long_noisy_phrase_outside_the_local_window(self):
        target = "孩子们一起在森林里快乐地唱歌回家"
        texts = [self.texts[0], self.texts[1], self.texts[2], target]
        acoustic = FakeAcoustic(texts + ["大" + target])
        tracker = CtcBookTracker(FakeIndex(texts), acoustic)
        tracker.locate(acoustic.scores(texts[0]))

        result = tracker.locate(acoustic.scores("大" + target))
        self.assertEqual((result["status"], result["segmentId"], result["alignmentMode"], result["editDistance"]),
                         ("relocated", "s4", "book_dp", 1))
        self.assertEqual([word["text"] for word in result["words"]],
                         [target[index:index + 2] for index in range(0, len(target), 2)])

    def test_book_dp_freezes_when_far_passages_are_equally_good(self):
        target = "孩子们一起在森林里快乐地唱歌回家"
        texts = [self.texts[0], self.texts[1], self.texts[2], target, target]
        acoustic = FakeAcoustic(texts + ["大" + target])
        tracker = CtcBookTracker(FakeIndex(texts), acoustic)
        tracker.locate(acoustic.scores(texts[0]))

        result = tracker.locate(acoustic.scores("大" + target))
        self.assertEqual((result["status"], result["segmentId"], result["reason"]),
                         ("uncertain", "s1", "ambiguous_book_alignment"))
        self.assertEqual(tracker.final_position, ("s1", 3))

    def test_book_dp_does_not_fuzzy_relocate_before_a_position_anchor(self):
        target = "孩子们一起在森林里快乐地唱歌回家"
        texts = [self.texts[0], self.texts[1], self.texts[2], target]
        acoustic = FakeAcoustic(texts + ["大" + target])
        tracker = CtcBookTracker(FakeIndex(texts), acoustic)
        result = tracker.locate(acoustic.scores("大" + target))
        self.assertEqual((result["status"], result["reason"]), ("uncertain", "reference_mismatch"))
        self.assertIsNone(tracker.final_position)

    def test_local_dp_omits_word_with_a_substitution_then_recovers(self):
        self.locate(self.texts[0])
        result = self.locate("小兔站在大树下门")
        self.assertEqual((result["status"], result["segmentId"], result["wordIndex"]), ("matched", "s2", 2))
        self.assertNotIn("下面", [word["text"] for word in result["words"]])
        recovered = self.locate(self.texts[1])
        self.assertEqual((recovered["status"], recovered["segmentId"], recovered["wordIndex"]),
                         ("matched", "s2", 3))

    def test_local_dp_freezes_when_nearby_references_tie(self):
        texts = ["大家一起高兴回家", "小熊带着雨伞出门", "小熊带着雨伞出门", "小兔站在大树下面"]
        acoustic = FakeAcoustic(texts + ["小熊带着大雨伞出门"])
        tracker = CtcBookTracker(FakeIndex(texts), acoustic)
        tracker.locate(acoustic.scores(texts[0]))
        result = tracker.locate(acoustic.scores("小熊带着大雨伞出门"))
        self.assertEqual((result["status"], result["segmentId"], result["reason"]),
                         ("uncertain", "s1", "ambiguous_local_alignment"))
        self.assertEqual(tracker.final_position, ("s1", 3))

    def test_anomaly_omission_does_not_invent_missing_word(self):
        self.locate(self.texts[0])
        result = self.locate("小兔大树下面")
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["words"], [])
        self.assertEqual(self.tracker.position, ("s1", 3))

    def test_anomaly_silence_and_foreign_sentence_freeze(self):
        self.locate(self.texts[0])
        for text, reason in (("", "silence"), ("今天午饭吃什么", "reference_mismatch")):
            result = self.locate(text)
            self.assertEqual((result["status"], result["segmentId"], result["reason"]), ("uncertain", "s1", reason))

    def test_ambiguous_passage_and_short_context_freeze(self):
        self.assertEqual(self.locate("小熊")["reason"], "insufficient_context")
        tracker = CtcBookTracker(FakeIndex([self.texts[0], self.texts[0]]), self.acoustic)
        self.assertEqual(tracker.locate(self.acoustic.scores(self.texts[0]))["reason"], "ambiguous_reference")
        self.assertIsNone(tracker.position)

    def test_bad_scores_and_padding_cannot_create_observed_words(self):
        with self.assertRaises(ValueError):
            forced_align([[float("nan"), 0]], [1])
        result = self.locate(self.texts[0], end_seconds=0.001)
        self.assertEqual(result["reason"], "incomplete_word")
        self.assertIsNone(self.tracker.position)

    def test_low_posterior_labels_are_not_a_calibrated_confidence(self):
        scores = self.acoustic.scores(self.texts[0])
        # Add many equally plausible alternatives while keeping the same greedy
        # transcript. Exact text alone must not authorize a position update.
        values = np.full((len(scores), 1000), 0.001)
        values[:, :scores.shape[1]] = np.exp(scores) * 0.01
        result = self.tracker.locate(np.log(values))
        self.assertEqual(result["reason"], "weak_acoustics")
        self.assertIsNone(self.tracker.position)

    def test_streaming_endpoints_and_repeat_across_encoder_chunk_boundaries(self):
        from types import SimpleNamespace
        scores = np.concatenate([self.acoustic.scores(self.texts[0]), self.acoustic.scores(self.texts[0]),
                                 self.acoustic.scores(self.texts[1])])
        chunks = [scores[start:start + 8] for start in range(0, len(scores), 8)]
        acoustic = self.acoustic
        class Stream:
            def accept(self, samples, final=False):
                return chunks.pop(0) if chunks else np.empty((0, len(acoustic.tokens)))
        acoustic.new_stream = Stream
        pack = SimpleNamespace(text_index=lambda: FakeIndex(self.texts))
        events = list(ctc_events(pack, np.zeros(math.ceil(len(scores) * 0.04 * 16000)), acoustic))
        completed = [(item["status"], item["segmentId"]) for item in events if item["final"]]
        self.assertEqual(completed, [("matched", "s1"), ("repeated", "s1"), ("matched", "s2")])
        self.assertTrue(any(not item["final"] and item["words"] for item in events))
        self.assertTrue(all(item["endSeconds"] <= len(scores) * 0.04 for item in events))

    def test_streaming_pause_split_uses_pending_short_fragment_for_position(self):
        first = self.acoustic.scores(self.texts[0][:3])
        second = self.acoustic.scores(self.texts[0][3:])
        scores = np.concatenate((first, second))
        chunks = [scores[start:start + 8] for start in range(0, len(scores), 8)]
        acoustic = self.acoustic

        class Stream:
            def accept(self, samples, final=False):
                return chunks.pop(0) if chunks else np.empty((0, len(acoustic.tokens)))

        acoustic.new_stream = Stream
        pack = type("Pack", (), {"text_index": lambda _: FakeIndex(self.texts)})()
        sample_count = len(scores) * round(acoustic.frame_seconds * 16000)
        events = list(ctc_events(pack, np.zeros(sample_count), acoustic))
        final_events = [item for item in events if item["final"]]
        self.assertEqual([item["status"] for item in final_events], ["uncertain", "matched"])
        self.assertEqual((final_events[-1]["segmentId"], final_events[-1]["wordIndex"]), ("s1", 3))
        self.assertEqual([word["text"] for word in final_events[-1]["words"]],
                         ["小熊", "带着", "雨伞", "出门"])

    def test_word_initial_aliases_and_rare_character_bytes_preserve_offsets(self):
        acoustic = ZipformerCtc.__new__(ZipformerCtc)
        acoustic.tokens = {0: "<blk>", 1: "▁", 2: "小", 3: "▁小", 4: "<0xE4>", 5: "<0xBC>", 6: "<0x9E>"}
        acoustic.token_ids = {value: key for key, value in acoustic.tokens.items()}
        acoustic._projection = [0, 0, 2, 2, 4, 5, 6]
        labels, spans = acoustic.alignment_tokens("小伞小伞")
        self.assertEqual((labels, spans), ([2, 4, 5, 6, 2, 4, 5, 6], [(0, 1), (1, 4), (4, 5), (5, 8)]))
        path = [0, 3, 0, 4, 5, 6, 0, 3, 0, 4, 5, 6, 0]
        values = np.full((len(path), len(acoustic.tokens)), 0.001)
        values[np.arange(len(path)), path] = 0.99
        scores = acoustic.project_scores(np.log(values))
        self.assertEqual(acoustic.decode_ids(greedy_ids(scores)), "小伞小伞")
        tracker = CtcBookTracker(FakeIndex(["小伞小伞"]), acoustic)
        result = tracker.locate(scores)
        self.assertEqual([(word["text"], word["textStart"], word["textEnd"]) for word in result["words"]],
                         [("小伞", 0, 2), ("小伞", 2, 4)])


if __name__ == "__main__":
    unittest.main()
