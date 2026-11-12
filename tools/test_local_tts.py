"""Format and optional Windows System.Speech integration checks for local TTS."""

import sys
import tempfile
import unittest
import wave
from pathlib import Path

from local_tts import _validate, synthesize


class LocalSpeechTests(unittest.TestCase):
    def test_windows_sapi_generates_nonempty_chinese_pcm16_wav(self):
        if sys.platform != "win32":
            self.skipTest("Windows System.Speech is unavailable on this platform")
        with tempfile.TemporaryDirectory(prefix="tongyue-local-tts-") as directory:
            result = synthesize("这是电脑本地离线语音合成测试。", directory)
            output = Path(result["path"])
            self.assertTrue(output.is_file())
            self.assertTrue(result["voice"])
            self.assertGreater(result["durationMs"], 0)
            self.assertGreater(_validate(output), 0)

    def test_validator_rejects_non_mono_16khz_pcm16(self):
        with tempfile.TemporaryDirectory(prefix="tongyue-invalid-tts-") as directory:
            path = Path(directory) / "wrong.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(bytes([1, 0, 1, 0]) * 80)
            with self.assertRaisesRegex(ValueError, "16 kHz"):
                _validate(path)


if __name__ == "__main__":
    unittest.main()
