"""Exercise the G4 latency analyzer with controlled stereo signals."""

import array
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from bench_question_latency import run


RATE = 16000
WINDOWS = {"questionWindowMs": [200, 1300], "answerWindowMs": [1000, 2600]}


def write_capture(path, answer_start_ms=1100, answer_end_ms=1600):
    frames = RATE * 3
    question_start, question_end = RATE // 2, RATE
    answer_start = round(answer_start_ms * RATE / 1000) if answer_start_ms is not None else frames
    answer_end = round(answer_end_ms * RATE / 1000) if answer_end_ms is not None else frames
    samples = array.array("h")
    for index in range(frames):
        question = 8192 if question_start <= index < question_end else 0
        answer = 8192 if answer_start <= index < answer_end else 0
        samples.extend((question, answer))
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, RATE, frames, "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())


class QuestionLatencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tongyue-g4-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write_manifest(self, cases):
        path = self.root / "cases.json"
        path.write_text(json.dumps({"captureKind": "synthetic", "cases": cases}), encoding="utf-8")
        return path

    @staticmethod
    def args(path, *, min_trials=20, target_ms=2000):
        return SimpleNamespace(cases=path, min_trials=min_trials, target_ms=target_ms,
                               question_dbfs=-35, answer_dbfs=-35)

    def test_twenty_independent_trials_measure_p95_and_pass_synthetic_gate(self):
        cases = []
        for index in range(20):
            recording = f"trial-{index:02}.wav"
            write_capture(self.root / recording, answer_start_ms=1100 + index * 20,
                          answer_end_ms=1600 + index * 20)
            cases.append({"recording": recording, **WINDOWS, "answerConfirmed": True})

        report = run(self.args(self.write_manifest(cases)))

        self.assertEqual(report["captureKind"], "synthetic")
        self.assertEqual((report["trials"], report["confirmedMeasurements"]), (20, 20))
        self.assertEqual(report["p95Ms"], 460.0)
        self.assertTrue(report["passesLatencyTarget"])

    def test_missing_answer_cannot_pass_by_discarding_the_trial(self):
        write_capture(self.root / "missing.wav", answer_start_ms=None, answer_end_ms=None)
        cases = [{"recording": "missing.wav", **WINDOWS, "answerConfirmed": True}]

        report = run(self.args(self.write_manifest(cases), min_trials=1))

        self.assertEqual(report["cases"][0]["status"], "answer_not_detected")
        self.assertFalse(report["passesLatencyTarget"])

    def test_answer_before_question_end_is_not_counted_as_a_measurement(self):
        write_capture(self.root / "early.wav", answer_start_ms=900, answer_end_ms=1500)
        cases = [{"recording": "early.wav", **WINDOWS, "answerWindowMs": [800, 2600],
                  "answerConfirmed": True}]

        report = run(self.args(self.write_manifest(cases), min_trials=1))

        self.assertEqual(report["cases"][0]["status"], "answer_precedes_question_end")
        self.assertEqual(report["confirmedMeasurements"], 0)
        self.assertFalse(report["passesLatencyTarget"])

    def test_same_recording_cannot_count_as_independent_trials(self):
        write_capture(self.root / "reused.wav")
        cases = [{"recording": "reused.wav", **WINDOWS, "answerConfirmed": True} for _ in range(2)]

        with self.assertRaisesRegex(ValueError, "one recording per independent question trial"):
            run(self.args(self.write_manifest(cases), min_trials=1))


if __name__ == "__main__":
    unittest.main()
