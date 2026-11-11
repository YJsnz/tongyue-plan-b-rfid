"""Exercise StoryPack uploads over the same HTTP handler used by the reader."""

import hashlib
import http.client
import io
import json
import math
import sqlite3
import struct
import tarfile
import tempfile
import threading
import unittest
import wave
from contextlib import closing
from unittest.mock import patch
from http.server import HTTPServer
from pathlib import Path

from rfid_simulator import Handler, Reader
from storypack import compile_pack, write_json
from storypack_import import MAX_ARCHIVE_BYTES, export


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


def archive_bytes(files, extra=()):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in files.items():
            entry = tarfile.TarInfo("packs/bear-friends/" + name)
            entry.size = len(payload)
            archive.addfile(entry, io.BytesIO(payload))
        for entry, payload in extra:
            archive.addfile(entry, io.BytesIO(payload))
    return buffer.getvalue()


def replace_json(files, name, value):
    files[name] = json.dumps(value, ensure_ascii=False).encode("utf-8")
    if name != "book.json":
        book = json.loads(files["book.json"])
        book["files"][name] = hashlib.sha256(files[name]).hexdigest()
        files["book.json"] = json.dumps(book, ensure_ascii=False).encode("utf-8")


class StoryPackImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="tongyue-import-test-")
        root = Path(cls.workspace.name)
        cls.source = root / "source"
        cls.source.mkdir()
        with wave.open(str(cls.source / "audio.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"".join(struct.pack("<h", round(3000 * math.sin(i * math.tau * 440 / 16000)))
                                       for i in range(16000)))
        write_json(cls.source / "book.json", {
            "bookId": "bear-friends", "version": "test-1", "title": "导入测试绘本",
            "language": "zh-CN", "rights": "原创测试素材",
            "segments": [{"segmentId": "s1", "text": "小熊带伞，因为要下雨。", "audio": "audio.wav"}],
            "faq": [{"segmentId": "s1", "questions": ["它为什么带伞？"], "answer": "因为要下雨。",
                     "evidence": "因为要下雨", "audio": "audio.wav"}],
            "voiceQuestions": [{"question": "它为什么带伞？", "audio": "audio.wav"}],
            "voicePrompts": {"unsupported": "audio.wav"}
        })
        compiled = compile_pack(cls.source / "book.json", root / "compiled")
        cls.files = {file.relative_to(compiled).as_posix(): file.read_bytes()
                     for file in compiled.rglob("*") if file.is_file()}
        cls.server = HTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join()
        cls.server.server_close()
        cls.workspace.cleanup()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=self.workspace.name)
        self.root = Path(self.folder.name)
        self.packs = self.root / "packs"
        self.packs.mkdir()
        self.server.reader = Reader(self.root / "state.json", 30, self.packs)

    def tearDown(self):
        self.folder.cleanup()

    def request(self, path, payload=None, method="POST", content_type="application/json", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        try:
            body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
            connection.request(method, path, body=body,
                               headers={"Content-Type": content_type, **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def upload(self, files=None, extra=(), raw=None):
        return self.request("/v1/storypacks", raw if raw is not None else archive_bytes(files or self.files, extra),
                            content_type="application/gzip")

    def assert_rejected(self, files=None, extra=(), raw=None):
        self.assertEqual(self.upload(files, extra, raw), (422, {"code": "PACK_INVALID"}))
        self.assertEqual(list(self.packs.iterdir()), [])
        self.assertEqual(self.request("/v1/books", method="GET"), (200, {"items": []}))

    def test_import_bind_read_answer_sync_and_restart(self):
        status, imported = self.upload()
        self.assertEqual(status, 200)
        self.assertFalse(imported["alreadyInstalled"])
        self.assertEqual(imported["version"], "test-1")
        self.assertEqual(self.request("/v1/books", method="GET")[1]["items"][0]["bookId"], "bear-friends")
        scan = self.request("/v1/scans")[1]
        self.request("/sim/tap", {"tagUid": "0004A12B"})
        self.assertEqual(self.request("/v1/book-bindings/0004A12B", {**scan, "bookId": "bear-friends"},
                                      method="PUT")[0], 200)
        self.assertEqual(self.request("/sim/tap", {"tagUid": "0004A12B"})[1]["status"], "started")
        answer = self.request("/sim/ask", {"question": "它为什么带伞？"})[1]
        self.assertEqual(answer["status"], "answered")
        self.assertEqual(answer["evidence"], "因为要下雨")
        session = self.request("/sim/finish")[1]
        self.server.reader = Reader(self.root / "state.json", 30, self.packs)
        batch = self.request("/v1/reading-sessions?limit=8", method="GET")[1]
        self.assertEqual(batch["items"], [session])
        self.assertEqual(batch["items"][0]["answers"][0]["answer"], "因为要下雨。")
        self.assertEqual(self.request("/v1/reading-sessions?after=" + batch["nextCursor"], method="GET")[1]["items"], [])
        self.assertEqual(self.request("/sim/tap", {"tagUid": "0004A12B"})[1]["status"], "started")

    def test_position_reviews_sync_separately_and_survive_reader_restart(self):
        event_id = "0123456789abcdef0123456789abcdef"
        session = {"sessionId": "review-session", "trajectory": [{"eventId": event_id}]}
        self.server.reader.save({**self.server.reader.state, "sessions": [session]})
        path = f"/v1/position-reviews/{session['sessionId']}/{event_id}"
        self.assertEqual(self.request("/v1/position-reviews", method="GET"), (200, {"items": []}))

        first = {"status": "needs_review", "note": "再和孩子核对", "updatedAtMs": 1000}
        saved = self.request(path, first, method="PUT")
        expected = {"deviceId": self.server.reader.state["deviceId"], "sessionId": "review-session",
                    "eventId": event_id, **first}
        self.assertEqual(saved, (200, expected))
        stale = {"status": "confirmed", "note": "旧设备上的状态", "updatedAtMs": 900}
        self.assertEqual(self.request(path, stale, method="PUT"), (200, expected))

        current = {"status": "position_mismatch", "note": "", "updatedAtMs": 2000}
        expected = {**expected, **current}
        self.assertEqual(self.request(path, current, method="PUT"), (200, expected))
        self.assertEqual(self.request("/v1/position-reviews", method="GET"), (200, {"items": [expected]}))
        self.assertEqual(self.request(f"/v1/position-reviews/{session['sessionId']}/" + "f" * 32,
                                      current, method="PUT"), (404, {"code": "POSITION_TRACE_NOT_FOUND"}))
        invalid = {**current, "status": "guessed"}
        self.assertEqual(self.request(path, invalid, method="PUT"), (400, {"code": "INVALID_POSITION_REVIEW"}))

        self.server.reader = Reader(self.root / "state.json", 30, self.packs)
        self.assertEqual(self.request("/v1/position-reviews", method="GET"), (200, {"items": [expected]}))

    def test_retry_is_idempotent_and_conflict_keeps_old_book(self):
        self.assertEqual(self.upload()[0], 200)
        self.assertTrue(self.upload()[1]["alreadyInstalled"])
        files = self.files.copy()
        manifest = json.loads(files["book.json"])
        manifest["version"] = "test-2"
        replace_json(files, "book.json", manifest)
        self.assertEqual(self.upload(files), (409, {"code": "PACK_ALREADY_EXISTS"}))
        self.assertEqual(self.request("/v1/books", method="GET")[1]["items"][0]["version"], "test-1")

    def test_exported_book_roundtrips(self):
        self.assertEqual(self.upload()[0], 200)
        output = self.root / "exported.tar.gz"
        export(self.server.reader.load_pack("bear-friends"), output)
        self.assertTrue(self.upload(raw=output.read_bytes())[1]["alreadyInstalled"])

    def test_wrong_hash_and_missing_declared_file(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                files = self.files.copy()
                name = "audio_cache/segment-s1.wav"
                if missing:
                    del files[name]
                else:
                    files[name] += b"changed"
                self.assert_rejected(files)

    def test_invalid_metadata_types_are_reported(self):
        cases = [("book.json", {**json.loads(self.files["book.json"]), "files": ["segments.json", "faq_index.sqlite"]}),
                 ("voice.json", {"questions": [], "prompts": []}),
                 ("segments.json", [None])]
        for name, metadata in cases:
            with self.subTest(name=name):
                files = self.files.copy()
                replace_json(files, name, metadata)
                self.assert_rejected(files)

    def test_archive_paths_links_duplicates_and_multiple_books(self):
        entries = []
        for name in ("packs/bear-friends/../../escape", "packs/another/book.json",
                     "packs/bear-friends/audio_cache/segment-S1.wav", "packs/bear-friends/extra.txt"):
            entry = tarfile.TarInfo(name)
            entry.size = 1
            entries.append(entry)
        link = tarfile.TarInfo("packs/bear-friends/audio_cache/link.wav")
        link.type, link.linkname = tarfile.SYMTYPE, "../../escape"
        entries.append(link)
        for entry in entries:
            with self.subTest(name=entry.name):
                self.assert_rejected(extra=[(entry, b"x")])
        self.assertFalse((self.root / "escape").exists())

    def test_truncated_and_invalid_compressed_data(self):
        valid = archive_bytes(self.files)
        for raw in (b"bad gzip", valid[:-8], valid[:len(valid) // 2]):
            with self.subTest(size=len(raw)):
                self.assert_rejected(raw=raw)

    def test_incomplete_wav_payload_is_rejected(self):
        files = self.files.copy()
        name = "audio_cache/segment-s1.wav"
        files[name] = files[name][:-20]
        manifest = json.loads(files["book.json"])
        manifest["files"][name] = hashlib.sha256(files[name]).hexdigest()
        replace_json(files, "book.json", manifest)
        self.assert_rejected(files)

    def test_failed_install_is_not_published_and_can_retry(self):
        with patch("storypack_import.os.fsync", side_effect=OSError("test storage failure")):
            self.assertEqual(self.upload(), (500, {"code": "IO_ERROR"}))
        self.assertEqual(list(self.packs.iterdir()), [])
        self.assertEqual(self.upload()[0], 200)

    def test_invalid_evidence_and_unapproved_voice_question(self):
        files = self.files.copy()
        database = self.root / "faq.sqlite"
        database.write_bytes(files["faq_index.sqlite"])
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE faq SET evidence='原文中没有这句话'")
        files["faq_index.sqlite"] = database.read_bytes()
        manifest = json.loads(files["book.json"])
        manifest["files"]["faq_index.sqlite"] = hashlib.sha256(files["faq_index.sqlite"]).hexdigest()
        replace_json(files, "book.json", manifest)
        self.assert_rejected(files)
        files = self.files.copy()
        voice = json.loads(files["voice.json"])
        voice["questions"][0]["question"] = "未审核的问题"
        replace_json(files, "voice.json", voice)
        self.assert_rejected(files)

    def test_expanded_size_and_entry_limits(self):
        with patch("storypack_import.MAX_UNPACKED_BYTES", 2048):
            self.assert_rejected()
        with patch("storypack_import.MAX_ARCHIVE_ENTRIES", 3):
            self.assert_rejected()

    def test_chinese_demo_import_locate_and_voice_answer(self):
        repository = Path(__file__).resolve().parent.parent
        demo = repository / "output" / "rfid-demo-indexed"
        locator = repository / "output" / "reader-pc" / "locate_wav.exe"
        archive = demo / "bear-friends-demo-pack.tar.gz"
        if not locator.is_file() or not archive.is_file():
            self.skipTest("Generate the indexed demo and PC locate_wav first")
        import os
        if os.name != "nt":
            self.skipTest("This optional demo uses the Windows locator; core upload tests run on Linux")
        self.server.reader = Reader(self.root / "state.json", 30, self.packs, locator, demo / "fixtures")
        self.assertEqual(self.upload(raw=archive.read_bytes())[0], 200)
        scan = self.request("/v1/scans")[1]
        self.request("/sim/tap", {"tagUid": "0004A12B"})
        self.assertEqual(self.request("/v1/book-bindings/0004A12B", {**scan, "bookId": "bear-friends"},
                                      method="PUT")[0], 200)
        self.request("/sim/tap", {"tagUid": "0004A12B"})
        located = self.request("/sim/locate", {"recording": "read-s2.wav"})[1]
        self.assertEqual(located["segmentId"], "s2")
        answer = self.request("/sim/ask-recording", {"recording": "question-2.wav"})[1]
        self.assertEqual(answer["status"], "answered")
        self.assertEqual(answer["segmentId"], "s2")
        self.assertTrue(answer["evidence"])
        for recording, expected in (("question-1.wav", "unsupported"), ("question-unknown.wav", "unsupported"),
                                    ("silence.wav", "repeatQuestion")):
            with self.subTest(recording=recording):
                self.assertEqual(self.request("/sim/ask-recording", {"recording": recording})[1]["status"], expected)
        session = self.request("/sim/finish")[1]
        self.assertEqual(session["lastSegmentId"], "s2")
        self.assertEqual(session["positionSource"], "audio")
        self.assertEqual(len(session["answers"]), 1)
        self.assertEqual(session["answers"][0]["source"], "voice")

    def test_upload_limits_media_type_and_busy(self):
        self.assertEqual(self.request("/v1/storypacks", b"x"), (415, {"code": "INVALID_PACK_UPLOAD"}))
        self.assertEqual(self.request("/v1/storypacks", b"", content_type="application/gzip"),
                         (400, {"code": "INVALID_BODY_LENGTH"}))
        self.assertEqual(self.request("/v1/storypacks", b"", content_type="application/gzip",
                                      headers={"Content-Length": str(MAX_ARCHIVE_BYTES + 1)}),
                         (413, {"code": "PACK_TOO_LARGE"}))
        with self.server.reader.import_lock:
            self.assertEqual(self.upload(raw=b"x"), (409, {"code": "PACK_IMPORT_BUSY"}))
        self.assertEqual(list(self.packs.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
