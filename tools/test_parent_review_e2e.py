"""Exercise two ArkTS parent clients against the live desktop reader API."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rfid_simulator import Handler, Reader  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get("TONGYUE_NODE") or shutil.which("node")
TYPESCRIPT = os.environ.get("TONGYUE_TYPESCRIPT")
if not TYPESCRIPT and sys.platform == "win32":
    candidate = Path(r"D:\DevEco Studio\sdk\default\openharmony\ets\build-tools\ets-loader\node_modules\typescript")
    if candidate.is_dir():
        TYPESCRIPT = str(candidate)


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


@unittest.skipUnless(NODE and TYPESCRIPT and Path(TYPESCRIPT).is_dir(),
                     "requires Node.js and the DevEco TypeScript package")
class ParentReviewEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tongyue-parent-review-e2e-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.state_path = root / "reader-state.json"
        self.server = HTTPServer(("127.0.0.1", 0), QuietHandler)
        self.server.reader = Reader(self.state_path, 30)
        event_id = "0123456789abcdef0123456789abcdef"
        session = {
            "sessionId": "session-e2e",
            "bookId": "bear-friends",
            "packVersion": "test-v1",
            "endedAtMs": 1000,
            "durationMs": 1000,
            "lastSegmentId": "s1",
            "positionSource": "ctc",
            "trajectory": [{"eventId": event_id, "offsetMs": 500, "segmentId": "s1",
                             "source": "ctc", "status": "matched", "segmentNumber": 1,
                             "segmentText": "小熊借伞。", "wordIndex": 0, "textStart": 0,
                             "textEnd": 2, "text": "小熊"}],
            "trajectoryDropped": 0,
            "traceStats": {"repeated": 0, "relocated": 0, "uncertain": 0}
        }
        self.server.reader.save({**self.server.reader.state, "deviceId": "reader-e2e", "sessions": [session]})
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)
        self.address = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.worker.join(timeout=5)
        self.server.server_close()

    def run_parent(self, phase):
        result = subprocess.run(
            [NODE, str(ROOT / "tools/test_parent_review_e2e.cjs"), TYPESCRIPT, self.address, phase],
            cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_two_parents_share_review_through_reader_and_survive_restart(self):
        first = self.run_parent("parent-a")
        self.assertEqual(first["reviews"][0]["status"], "position_mismatch")
        self.assertEqual(first["reviews"][0]["note"], "读到了下一页的内容")

        self.server.reader = Reader(self.state_path, 30)
        self.assertEqual(self.server.reader.state["positionReviews"], first["reviews"])

        second = self.run_parent("parent-b")
        self.assertEqual(second["parentBRead"], first["reviews"])
        self.assertEqual(second["parentARefresh"][0]["status"], "confirmed")
        self.assertEqual(second["parentARefresh"][0]["note"], "已与孩子核对，读者位置有误")
        self.assertEqual(second["originalTraceStatus"], "matched")

        self.server.reader = Reader(self.state_path, 30)
        self.assertEqual(self.server.reader.state["positionReviews"], second["parentARefresh"])


if __name__ == "__main__":
    unittest.main()
