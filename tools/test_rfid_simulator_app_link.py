import tempfile
import threading
import time
import unittest
import wave
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from rfid_simulator import ApiError, Handler, Reader


class FakePack:
    def __init__(self, root):
        self.directory = root / "pack"
        (self.directory / "audio_cache").mkdir(parents=True)
        self.segments = [
            {"segmentId": "s1", "text": "小熊带着一把伞。", "audio": "audio_cache/segment-s1.wav"},
            {"segmentId": "s2", "text": "小兔在树下等雨停。", "audio": "audio_cache/segment-s2.wav"},
        ]
        answer_audio = "audio_cache/answer-1.wav"
        self.book = {"title": "小熊找朋友", "files": {
            segment["audio"]: "unused" for segment in self.segments
        } | {answer_audio: "unused"}}
        self.answer_path = answer_audio
        self.answer_result = {"status": "answered", "segmentId": "s1", "question": "带了什么",
                              "answer": "带的是一把伞。", "evidence": "小熊带着一把伞。",
                              "audio": answer_audio}
        for relative in self.book["files"]:
            self.write_wav(self.directory / relative, 440)

    @staticmethod
    def write_wav(path, frequency):
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x10\x00" * 160)

    def answer(self, _segment_id, _question):
        return dict(self.answer_result)

    def answer_free(self, _segment_id, _question):
        result = dict(self.answer_result)
        result.pop("audio", None)
        result["answerMode"] = "semantic-template"
        return result


class SimulatorAppLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pack = FakePack(self.root)
        self.reader = Reader.__new__(Reader)
        self.reader.packs = None
        self.reader.allow_free_questions = False
        self.reader.local_tts_dir = self.root / "tts"
        self.reader.active = {
            "sessionId": "session-test", "bookId": "bear-friends", "tagUid": "0004A12C",
            "started": time.monotonic(), "pack": self.pack, "segmentId": "s1", "positionSource": "manual",
            "positionUncertain": False, "answers": [], "answerAudios": [], "trajectory": [],
            "trajectoryDropped": 0, "traceStats": {"repeated": 0, "relocated": 0, "uncertain": 0},
            "wordIndex": None, "ctcTracker": None,
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_reading_state_and_segment_navigation_return_current_text(self):
        state = self.reader.dispatch("GET", type("Target", (), {"path": "/sim/reading", "query": ""})(), {})
        self.assertEqual(state["bookTitle"], "小熊找朋友")
        self.assertEqual(state["segmentNumber"], 1)
        self.assertEqual(state["text"], "小熊带着一把伞。")
        selected = self.reader.select_segment("s2")
        self.assertEqual(selected["text"], "小兔在树下等雨停。")
        self.assertEqual(self.reader.reading_state()["segmentIds"], ["s1", "s2"])

    def test_pack_audio_routes_return_only_manifest_files(self):
        segment_audio = self.reader.audio_bytes("/sim/audio/current")
        faq_audio = self.reader.audio_bytes("/sim/audio/pack/" + self.pack.answer_path)
        self.assertTrue(segment_audio.startswith(b"RIFF"))
        self.assertTrue(faq_audio.startswith(b"RIFF"))
        with self.assertRaises(ApiError) as failure:
            self.reader.audio_bytes("/sim/audio/pack/audio_cache/../book.json")
        self.assertEqual(failure.exception.code, "AUDIO_NOT_FOUND")

    def test_answer_returns_scoped_audio_url_and_audio_bytes(self):
        self.reader.allow_free_questions = True
        self.reader.local_tts_dir.mkdir()

        def synthesize(_text, output_dir):
            path = Path(output_dir) / "answer-0123456789abcdef0123456789abcdef.wav"
            FakePack.write_wav(path, 600)
            return {"path": str(path), "voice": "test", "durationMs": 10}

        with patch("local_tts.synthesize", side_effect=synthesize):
            result = self.reader.ask("小熊带了什么？")
        self.assertEqual(result["audioUrl"], "/sim/audio/answer-0123456789abcdef0123456789abcdef.wav")
        audio = self.reader.audio_bytes(result["audioUrl"])
        self.assertTrue(audio.startswith(b"RIFF"))
        self.reader.active["answerAudios"].clear()
        with self.assertRaises(ApiError) as failure:
            self.reader.audio_bytes(result["audioUrl"])
        self.assertEqual(failure.exception.code, "AUDIO_NOT_FOUND")

    def test_http_audio_route_returns_wav_content_type_and_bytes(self):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.reader = self.reader
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/sim/audio/current") as response:
                body = response.read()
                self.assertEqual(response.headers.get_content_type(), "audio/wav")
                self.assertEqual(int(response.headers["Content-Length"]), len(body))
                self.assertTrue(body.startswith(b"RIFF"))
            with self.assertRaises(HTTPError) as failure:
                urlopen(f"http://127.0.0.1:{server.server_port}/sim/audio/nope.wav")
            self.assertEqual(failure.exception.code, 404)
            failure.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
