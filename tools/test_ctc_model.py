"""Opt-in real CPU model/Chinese TTS integration; no implicit network downloads."""

import os
import unittest
import tempfile
from pathlib import Path

from ctc_alignment import CtcBookTracker, greedy_ids, readable
from reading_probe import ctc_events
from storypack import StoryPack


@unittest.skipUnless(os.environ.get("TONGYUE_CTC_MODEL"), "Set TONGYUE_CTC_MODEL for the optional PC acoustic model")
class RealCtcModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        from ctc_acoustic import ZipformerCtc, read_pcm_wav
        cls.np, cls.read_wav = np, staticmethod(read_pcm_wav)
        cls.directory = Path(os.environ["TONGYUE_CTC_MODEL"])
        cls.demo = Path(os.environ["TONGYUE_CTC_DEMO"])
        cls.acoustic = ZipformerCtc(cls.directory)
        cls.pack = StoryPack(cls.demo / "packs", "bear-friends")

    def samples(self, name):
        return self.read_wav(self.demo / "fixtures" / name)

    def test_actual_streaming_word_times_repeat_foreign_question_and_silence(self):
        for filename, expected in (
                ("read-s1.wav", [("matched", "s1")]),
                ("read-s2.wav", [("matched", "s2")]),
                ("read-repeat.wav", [("matched", "s1"), ("repeated", "s1"), ("matched", "s2")]),
                ("question-unknown.wav", [("uncertain", None)]), ("silence.wav", [])):
            with self.subTest(recording=filename):
                samples = self.samples(filename)
                events = list(ctc_events(self.pack, samples, self.acoustic))
                completed = [item for item in events if item["final"]]
                self.assertEqual([(item["status"], item["segmentId"]) for item in completed], expected)
                for item in completed:
                    if item["status"] == "uncertain":
                        self.assertEqual(item["reason"], "reference_mismatch")
                        self.assertEqual(item["words"], [])
                        continue
                    record = next(record for record in self.pack.text_index().segments if record["segmentId"] == item["segmentId"])
                    self.assertEqual(len(item["words"]), len(record["words"]))
                    self.assertEqual(item["wordIndex"], len(record["words"]) - 1)
                    previous = 0
                    for word in item["words"]:
                        self.assertEqual(word["text"], record["text"][word["textStart"]:word["textEnd"]])
                        self.assertLessEqual(previous, word["startSeconds"])
                        self.assertLess(word["startSeconds"], word["endSeconds"])
                        self.assertLessEqual(word["endSeconds"], len(samples) / 16000)
                        previous = word["endSeconds"]

    def test_real_syllable_beam_tracks_exact_words_after_an_anchor(self):
        samples = self.samples("read-s2.wav")
        stream, chunks = self.acoustic.new_stream(), []
        for offset in range(0, len(samples), 1600):
            chunks.append(stream.accept(samples[offset:offset + 1600], final=offset + 1600 >= len(samples)))
        scores = self.np.concatenate(chunks)[:int(self.np.ceil(len(samples) / 640))]

        index = self.pack.text_index()
        tracker = CtcBookTracker(index, self.acoustic)
        first = next(record for record in index.segments if record["segmentId"] == "s1")
        tracker.final_position = ("s1", len(first["words"]) - 1)
        result = tracker._syllable_beam(scores, 0.0)

        self.assertEqual((result["status"], result["segmentId"], result["wordIndex"]),
                         ("matched", "s2", 8))
        self.assertEqual([word["text"] for word in result["words"]],
                         ["小熊", "把", "递给", "因为", "没有", "带"])
        record = next(record for record in index.segments if record["segmentId"] == result["segmentId"])
        self.assertTrue(all(word["text"] == record["text"][word["textStart"]:word["textEnd"]]
                            for word in result["words"]))

    def test_feature_frontend_and_transcript_agree_with_official_runtime(self):
        # Optional reference-only package: pip install sherpa-onnx==2.0.1.
        import sherpa_onnx
        recognizer = sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(
            model=str(self.directory / "model.int8.onnx"), tokens=str(self.directory / "tokens.txt"), num_threads=1)
        for index in (1, 2):
            with self.subTest(segment=index):
                samples = self.samples(f"read-s{index}.wav")
                stream, chunks = self.acoustic.new_stream(), []
                for offset in range(0, len(samples), 1600):
                    chunks.append(stream.accept(samples[offset:offset + 1600], final=offset + 1600 >= len(samples)))
                scores = self.np.concatenate(chunks)[:int(self.np.ceil(len(samples) / 640))]
                decoded = readable(self.acoustic.decode_ids(greedy_ids(scores)))
                official = recognizer.create_stream()
                official.accept_waveform(16000, self.np.concatenate([samples, self.np.zeros(16000, dtype=self.np.float32)]))
                official.input_finished()
                while recognizer.is_ready(official):
                    recognizer.decode_stream(official)
                self.assertEqual(decoded, readable(recognizer.get_result(official)))
                self.assertEqual(decoded, readable(self.pack.segments[index - 1]["text"]))

    def test_real_ctc_updates_session_faq_gate_and_durable_sync_history(self):
        from rfid_simulator import Reader
        with tempfile.TemporaryDirectory(prefix="tongyue-ctc-session-") as temporary:
            state = Path(temporary) / "state.json"
            reader = Reader(state, 30, self.demo / "packs", recordings=(self.demo / "fixtures").resolve(),
                            ctc_acoustic=self.acoustic)
            reader.save({**reader.state, "bindings": {"0004A12B": "bear-friends"}})
            reader.tap("0004A12B")
            reader.locate("read-s1.wav")
            self.assertEqual(reader.active["wordIndex"], 9)
            self.assertEqual(reader.ask("小熊为什么带伞？")["status"], "answered")
            reader.locate("read-s1.wav")
            self.assertEqual(reader.active["traceStats"]["repeated"], 1)
            reader.locate("question-unknown.wav")
            self.assertEqual(reader.ask("小熊为什么带伞？")["status"], "needsPositionConfirmation")
            session = reader.finish(None)
            self.assertTrue(session["positionUncertain"])
            self.assertEqual(session["lastWordIndex"], 9)
            self.assertEqual([item["status"] for item in session["trajectory"]],
                             ["selected", "matched", "repeated", "uncertain"])
            self.assertEqual(session["trajectory"][1]["text"], "了")
            self.assertEqual(Reader(state, 30).sessions("")["items"], [session])

    def test_opt_in_free_voice_question_uses_page_evidence_and_never_saves_raw_transcript(self):
        from rfid_simulator import Reader
        with tempfile.TemporaryDirectory(prefix="tongyue-free-question-") as temporary:
            state = Path(temporary) / "state.json"
            reader = Reader(state, 30, self.demo / "packs", recordings=(self.demo / "fixtures").resolve(),
                            ctc_acoustic=self.acoustic, allow_free_questions=True)
            reader.save({**reader.state, "bindings": {"0004A12B": "bear-friends"}})
            reader.tap("0004A12B")
            result = reader.ask_recording("question-free.wav")
            self.assertEqual(result["status"], "answered")
            self.assertEqual(result["answerMode"], "extractive")
            self.assertEqual(result["evidence"], "小熊带着一把伞出门，因为天要下雨了。")
            reader.remember_answer(result, "voice")
            self.assertEqual(reader.ask_recording("question-unknown.wav")["status"], "unsupported")
            session = reader.finish(None)
            self.assertEqual(session["answers"][0]["question"], "原文检索提问")
            self.assertNotIn("小熊出门的时候带了什么东西", session["answers"][0]["question"])


if __name__ == "__main__":
    unittest.main()
