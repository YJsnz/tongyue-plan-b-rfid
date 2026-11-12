import io
import json
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication
    from qt_debug_station import DebugStation, ROOT, request_json
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "install tools/requirements-qt-debug.txt for the optional Qt smoke test")
class QtDebugStationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, condition, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.02)
        self.fail("Qt action did not finish before timeout")

    def test_local_server_and_reading_controls(self):
        packs = ROOT / "output" / "rfid-semantic-qa-demo" / "packs"
        if not packs.is_dir():
            self.skipTest("build output/rfid-semantic-qa-demo StoryPack first")

        with tempfile.TemporaryDirectory(prefix="tongyue-qt-test-") as directory:
            temporary = Path(directory)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]

            window = DebugStation(auto_connect=False)
            window.state_file = temporary / "reader-state.json"
            window.tts_directory = temporary / "tts"
            window.packs_input.setText(str(packs))
            window.port_input.setText(str(port))
            window.address_input.setText(f"http://127.0.0.1:{port}")
            window.show()
            try:
                window.start_server()
                self.wait_for(lambda: "设备：" in window.status_label.text())
                self.assertGreater(window.book_select.count(), 0)

                book_index = window.book_select.findData("bear-friends")
                self.assertGreaterEqual(book_index, 0)
                window.book_select.setCurrentIndex(book_index)
                window.uid_input.setText("A1B2C3D4")
                window.bind_tag()
                self.wait_for(lambda: not window.pending_action)
                self.assertIn("已绑定", window.log_view.toPlainText())

                window.simulate_tap()
                self.wait_for(lambda: not window.pending_action)
                self.assertTrue(window.reading.get("active"))
                self.assertEqual(window.reading.get("segmentNumber"), 1)

                window.move_segment(1)
                self.wait_for(lambda: not window.pending_action)
                self.assertEqual(window.reading.get("segmentNumber"), 2)

                played_audio = []
                window._play_wav = lambda audio: played_audio.append(audio)
                window.play_story_audio()
                self.wait_for(lambda: not window.pending_action)
                self.assertTrue(played_audio[-1].startswith(b"RIFF"))

                window.question_input.setText("小熊出门时随身携带的是什么？")
                window.ask_question()
                self.wait_for(lambda: not window.pending_action)
                self.assertIn("回答：", window.status_label.text())
                if window.answer_audio_url:
                    window.play_answer_audio()
                    self.wait_for(lambda: not window.pending_action)
                    self.assertTrue(played_audio[-1].startswith(b"RIFF"))

                window.finish_reading()
                self.wait_for(lambda: not window.pending_action)
                self.assertIn("1 条最近记录", window.status_label.text())
                self.assertFalse(window.reading.get("active"))

                window.stop_server()
                self.wait_for(lambda: window.server_process.state() == QProcess.ProcessState.NotRunning)

                window.refresh_status()
                self.wait_for(lambda: not window.pending_action)
                self.assertIn("无法连接电脑服务", window.status_label.text())
                window.packs_input.setText(str(temporary / "missing-packs"))
                window.start_server()
                self.assertIn("找不到 StoryPack 目录", window.log_view.toPlainText())
            finally:
                window.close()
                self.app.processEvents()

    def test_api_error_body_cannot_put_a_key_in_the_gui_error_message(self):
        body = json.dumps({"code": "QA_PROVIDER_FAILED", "apiKey": "test-secret-key"}).encode("utf-8")
        failure = HTTPError("https://api.deepseek.com/chat/completions", 502, "bad gateway", None,
                            io.BytesIO(body))
        with patch("qt_debug_station.urlopen", side_effect=failure):
            with self.assertRaises(RuntimeError) as error:
                request_json("http://127.0.0.1:8765", "POST", "/sim/ask", {"question": "test"})
        self.assertIn("原文核验", str(error.exception))
        self.assertNotIn("test-secret-key", str(error.exception))


if __name__ == "__main__":
    unittest.main()
