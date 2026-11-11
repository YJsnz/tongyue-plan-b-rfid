"""Reading history integrity, bounds and durable finish without acoustic dependencies."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rfid_simulator import Reader, StateWriteError


class ReadingTraceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tongyue-trace-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reader = Reader(self.root / "state.json", 30)
        segments = [{"segmentId": "s1", "text": "小熊带伞。", "words": [(0, 2, 0), (2, 4, 1)]},
                    {"segmentId": "s2", "text": "小兔回家。", "words": [(0, 2, 0), (2, 4, 1)]}]
        self.pack = SimpleNamespace(book={"version": "v1"}, segments=segments,
                                    text_index=lambda: SimpleNamespace(segments=segments))
        self.reader.load_pack = lambda _: self.pack
        self.reader.save({**self.reader.state, "bindings": {"0004A12B": "book-1"}})
        self.reader.tap("0004A12B")

    def event(self, **updates):
        value = {"formatVersion": 1, "backend": "ctc", "status": "matched", "segmentId": "s1", "final": True,
                 "startSeconds": 0, "endSeconds": 0.5, "wordIndex": 0,
                 "words": [{"wordIndex": 0, "textStart": 0, "textEnd": 2, "text": "小熊",
                            "startSeconds": 0.1, "endSeconds": 0.3}]}
        return {**value, **updates}

    def test_partial_progress_is_not_double_counted_and_unknown_freezes_position(self):
        self.reader.apply_alignment_event(self.event(final=False))
        self.assertEqual(len(self.reader.active["trajectory"]), 1)
        self.reader.apply_alignment_event(self.event())
        self.reader.apply_alignment_event(self.event(status="uncertain", words=[], segmentId=None))
        self.assertEqual(self.reader.active["segmentId"], "s1")
        self.assertEqual(self.reader.active["wordIndex"], 0)
        self.assertTrue(self.reader.active["positionUncertain"])
        session = self.reader.finish(None)
        self.assertEqual([item["status"] for item in session["trajectory"]], ["selected", "matched", "uncertain"])
        self.assertEqual(session["traceStats"], {"repeated": 0, "relocated": 0, "uncertain": 1})
        self.assertTrue(session["positionUncertain"])
        self.assertEqual(session["lastWordIndex"], 0)
        self.assertNotIn("wordIndex", session["trajectory"][-1])
        for item in session["trajectory"]:
            self.assertLessEqual(item["offsetMs"], session["durationMs"])
            self.assertNotIn("audio", item)
        restarted = Reader(self.root / "state.json", 30)
        self.assertEqual(restarted.sessions("")["items"], [session])

    def test_invalid_ctc_events_cannot_mutate_position_or_history(self):
        bad = [self.event(segmentId="outside"), self.event(endSeconds=float("nan")),
               self.event(words=[]), self.event(wordIndex=1), self.event(final=1)]
        for field, value in (("text", "小兔"), ("textEnd", 3), ("wordIndex", -1), ("endSeconds", 5)):
            item = self.event()
            item["words"][0][field] = value
            bad.append(item)
        for item in bad:
            with self.subTest(event=item):
                before = (self.reader.active["segmentId"], self.reader.active["wordIndex"],
                          copy.deepcopy(self.reader.active["trajectory"]))
                with self.assertRaises(ValueError):
                    self.reader.apply_alignment_event(item)
                self.assertEqual(before, (self.reader.active["segmentId"], self.reader.active["wordIndex"],
                                          self.reader.active["trajectory"]))

    def test_mfcc_manual_and_word_positions_are_distinct(self):
        self.reader.apply_alignment_event(self.event())
        self.reader.apply_locator_event("1000\tmatched\ts2\t2\t3")
        self.assertIsNone(self.reader.active["wordIndex"])
        self.reader.select_segment("s1")
        trace = self.reader.active["trajectory"]
        self.assertEqual([item["source"] for item in trace], ["manual", "ctc", "mfcc", "manual"])
        self.assertEqual(trace[1]["text"], "小熊")
        self.assertEqual(trace[2]["segmentText"], "小兔回家。")
        self.assertNotIn("wordIndex", trace[2])
        with self.assertRaises(ValueError):
            self.reader.apply_locator_event("nan\tmatched\ts2\t2\t3")
        with self.assertRaises(ValueError):
            self.reader.apply_locator_event("1000\tfabricated\ts2\t2\t3")

    def test_bounded_history_discloses_drops_and_retains_whole_session_counts(self):
        for _ in range(140):
            self.reader.apply_alignment_event(self.event(status="repeated"))
        session = self.reader.finish(None)
        self.assertEqual(len(session["trajectory"]), 128)
        self.assertEqual(session["trajectoryDropped"], 13)
        self.assertEqual(session["traceStats"]["repeated"], 140)
        self.assertEqual(len({item["eventId"] for item in session["trajectory"]}), 128)

    def test_failed_finish_keeps_reviewable_trace_and_retry_does_not_duplicate_it(self):
        self.reader.apply_alignment_event(self.event())
        before = copy.deepcopy(self.reader.active["trajectory"])
        with patch.object(self.reader, "save", side_effect=StateWriteError("disk full")):
            with self.assertRaises(StateWriteError):
                self.reader.finish(None)
        self.assertEqual(before, self.reader.active["trajectory"])
        self.assertEqual(self.reader.state["sessions"], [])
        self.assertEqual(self.reader.finish(None)["trajectory"], before)


if __name__ == "__main__":
    unittest.main()
