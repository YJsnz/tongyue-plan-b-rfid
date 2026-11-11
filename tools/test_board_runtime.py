"""Run the real Linux reader with a virtual PN532 UART, evdev keys and ALSA replacements.

This tests software integration only. It neither opens a microphone nor verifies physical hardware.
"""

import http.client
import json
import os
import secrets
import shlex
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from urllib.parse import urlsplit

if os.name == "posix":
    import math
    import pty
    import select
    from storypack import compile_pack, write_json
    from storypack_import import export
    from reader_keys import INPUT_EVENT


class VirtualPn532:
    """A UART peer that checks host checksums and sends ordinary PN532 responses."""

    def __init__(self):
        self.master, self.slave = pty.openpty()
        self.path = os.ttyname(self.slave)
        self.condition = threading.Condition()
        self.uid = None
        self.empty_polls = self.polls = 0
        self.corrupt_next = False
        self.errors = []
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.run)
        self.worker.start()

    def close(self):
        self.stop.set()
        self.worker.join(2)
        os.close(self.master)
        os.close(self.slave)

    def tag(self, uid):
        with self.condition:
            self.uid = bytes.fromhex(uid) if uid else None
            self.empty_polls = 0
            if uid is None and not self.condition.wait_for(lambda: self.empty_polls >= 3 or self.errors, timeout=5):
                raise AssertionError("Reader did not poll the empty UART")

    def hold_for_polls(self, count=3):
        with self.condition:
            target = self.polls + count
            if not self.condition.wait_for(lambda: self.polls >= target or self.errors, timeout=5):
                raise AssertionError("Reader stopped polling the UART")

    def run(self):
        pending = bytearray()
        try:
            while not self.stop.is_set():
                if not select.select([self.master], [], [], 0.05)[0]:
                    continue
                pending.extend(os.read(self.master, 4096))
                while True:
                    start = pending.find(b"\x00\x00\xff")
                    if start < 0:
                        pending = pending[-2:]
                        break
                    del pending[:start]
                    if len(pending) < 6:
                        break
                    size, checksum = pending[3:5]
                    if size == 0 and checksum == 255:
                        del pending[:6]  # Host abort ACK following an injected bad response.
                        continue
                    if (size + checksum) & 255 or not 1 <= size <= 254:
                        raise AssertionError("Invalid PN532 host length checksum")
                    if len(pending) < size + 7:
                        break
                    packet = bytes(pending[5:size + 7])
                    del pending[:size + 7]
                    if packet[-1] or sum(packet[:-1]) & 255 or packet[0] != 0xD4:
                        raise AssertionError("Invalid PN532 host frame")
                    code = packet[1]
                    corrupt = False
                    if code == 0x02:
                        body = b"\x32\x01\x06\x07"
                    elif code in (0x14, 0x32):
                        body = b""
                    elif code == 0x4A:
                        with self.condition:
                            self.polls += 1
                            self.empty_polls = self.empty_polls + 1 if self.uid is None else 0
                            body = b"\0" if self.uid is None else b"\x01\x01\x04\x00\x08" + bytes([len(self.uid)]) + self.uid
                            corrupt, self.corrupt_next = self.corrupt_next, False
                            self.condition.notify_all()
                    else:
                        raise AssertionError(f"Unexpected PN532 command {code}")
                    payload = bytes((0xD5, code + 1)) + body
                    reply = (b"\x00\x00\xff" + bytes((len(payload), -len(payload) & 255)) + payload
                             + bytes(((-sum(payload) + int(corrupt)) & 255, 0)))
                    os.write(self.master, b"\x00\x00\xff\x00\xff\x00" + reply)
        except BaseException as error:
            with self.condition:
                self.errors.append(str(error))
                self.condition.notify_all()


@unittest.skipUnless(os.name == "posix", "The actual board process requires Linux/POSIX")
class BoardRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.locator_command = shlex.split(os.environ.get("TONGYUE_LOCATOR_COMMAND", ""))
        if not self.locator_command or not shutil.which("openssl"):
            self.skipTest("Set TONGYUE_LOCATOR_COMMAND and install openssl for the virtual-board check")
        self.locator_command[0] = str(Path(shutil.which(self.locator_command[0]) or self.locator_command[0]).resolve())
        if Path(self.locator_command[-1]).is_file():
            self.locator_command[-1] = str(Path(self.locator_command[-1]).resolve())
        self.workspace = tempfile.TemporaryDirectory(prefix="tongyue-runtime-test-")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.programs = self.root / "bin"
        self.programs.mkdir()
        self.locator = self.programs / "locate_wav"
        self.locator.write_text("#!/bin/sh\nexec " + shlex.join(self.locator_command) + ' "$@"\n', encoding="utf-8")
        self.locator.chmod(0o755)
        self.make_book()
        self.make_audio_programs()
        self.packs = self.root / "installed"
        self.packs.mkdir()
        self.token = secrets.token_urlsafe(32)
        (self.root / "token").write_text(self.token, encoding="ascii")
        (self.root / "token").chmod(0o600)
        self.certificate, self.tls_key = self.root / "cert.pem", self.root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(self.tls_key), "-out", str(self.certificate)], capture_output=True, check=True, timeout=10)
        self.tls = ssl.create_default_context(cafile=str(self.certificate))
        self.uart = VirtualPn532()
        self.addCleanup(self.uart.close)
        self.fifo = self.root / "keys"
        os.mkfifo(self.fifo)
        self.keys = os.open(self.fifo, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, self.keys)
        self.process = None
        self.addCleanup(self.stop_reader)
        self.events = []
        self.start_reader()

    def make_book(self):
        for name, frequency in (("s1", 400), ("s2", 1800), ("q1", 700), ("q2", 2500),
                                ("foreign", 3300)):
            pcm = b"".join(struct.pack("<h", round(3000 * math.sin(i * math.tau * frequency / 16000)))
                           for i in range(16000)) + b"\0" * 25600
            with wave.open(str(self.root / (name + ".wav")), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(pcm)
        self.question = "它为什么把伞给小兔？"
        write_json(self.root / "book.json", {"bookId": "test-book", "version": "test-1", "title": "小熊借伞",
            "language": "zh-CN", "rights": "原创测试文本和合成测试声波",
            "segments": [{"segmentId": "s1", "text": "小熊带着伞，因为天要下雨了。", "audio": "s1.wav"},
                         {"segmentId": "s2", "text": "小熊把伞给小兔，因为小兔没有带伞。", "audio": "s2.wav"}],
            "faq": [{"segmentId": "s1", "questions": ["它为什么带伞？"], "answer": "因为天要下雨了。",
                     "evidence": "因为天要下雨了", "audio": "s1.wav"},
                    {"segmentId": "s2", "questions": [self.question], "answer": "因为小兔没有带伞。",
                     "evidence": "因为小兔没有带伞", "audio": "s2.wav"}],
            "voiceQuestions": [{"question": "它为什么带伞？", "audio": "q1.wav"},
                               {"question": self.question, "audio": "q2.wav"}],
            "voicePrompts": {"questionReady": "q1.wav", "needsPositionConfirmation": "q1.wav"}})
        compile_pack(self.root / "book.json", self.root / "authored", self.locator)
        from storypack import StoryPack
        self.archive = self.root / "test-book.tar.gz"
        export(StoryPack(self.root / "authored", "test-book"), self.archive)

    def configure_audio(self, reading, recording="s2.wav"):
        temporary = self.root / "audio.tmp"
        write_json(temporary, {"reading": str(self.root / reading) if reading else None,
                              "recording": str(self.root / recording), "question": str(self.root / "q2.wav")})
        temporary.replace(self.root / "audio.json")

    def make_audio_programs(self):
        self.configure_audio("s2.wav")
        capture = self.programs / "arecord"
        capture.write_text(f"#!{sys.executable}\n" + """
import json, os, shutil, sys, time, wave
from pathlib import Path
root = Path(os.environ['TONGYUE_RUNTIME_FIXTURE'])
config = json.loads((root / 'audio.json').read_text())
if 'wav' in sys.argv:
    shutil.copyfile(config['recording'], sys.argv[-1])
    with (root / 'capture.jsonl').open('a') as output:
        output.write(json.dumps({'path': sys.argv[-1]}) + '\\n')
    sys.exit(0)
source = config['question'] if '-d' in sys.argv else config['reading']
if source:
    with wave.open(source, 'rb') as audio:
        while data := audio.readframes(320):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            time.sleep(0.002)
if '-d' not in sys.argv:
    while True:
        sys.stdout.buffer.write(b'\\0' * 640)
        sys.stdout.buffer.flush()
        time.sleep(0.02)
""", encoding="utf-8")
        playback = self.programs / "aplay"
        playback.write_text(f"#!{sys.executable}\n" + """
import json, os, sys, time
from pathlib import Path
root = Path(os.environ['TONGYUE_RUNTIME_FIXTURE'])
name = Path(sys.argv[-1]).name
def log(kind):
    with (root / 'playback.jsonl').open('a') as output:
        output.write(json.dumps({'kind': kind, 'file': name, 'pid': os.getpid()}) + '\\n')
log('start')
if name.startswith('answer-'):
    while (root / 'hold-answer').exists():
        time.sleep(0.02)
time.sleep(0.05)
log('end')
""", encoding="utf-8")
        capture.chmod(0o755)
        playback.chmod(0o755)

    def start_reader(self):
        self.events = []
        self.stderr = (self.root / "reader.stderr").open("a", encoding="utf-8")
        environment = {**os.environ, "PATH": str(self.programs) + os.pathsep + os.environ["PATH"],
                       "TONGYUE_RUNTIME_FIXTURE": str(self.root)}
        self.process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("board_reader.py")),
            "--uart", self.uart.path, "--state", str(self.root / "state.json"), "--packs", str(self.packs),
            "--locator", str(self.locator), "--continuous", "--port", "0", "--tls-cert", str(self.certificate),
            "--tls-key", str(self.tls_key), "--pairing-token-file", str(self.root / "token"),
            "--keys-device", str(self.fifo), "--key-previous", "105", "--key-next", "106", "--key-ask", "28",
            "--key-finish", "1"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
            encoding="utf-8", env=environment)
        process = self.process
        events = self.events

        def read_events():
            for line in process.stdout:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    events.append({"unexpectedOutput": line})

        self.worker = threading.Thread(target=read_events)
        self.worker.start()
        api = self.wait_event(lambda event: "api" in event)
        self.port = urlsplit(api["api"]).port
        self.wait_event(lambda event: event.get("rfid") == "ready")

    def stop_reader(self):
        if self.process is None:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            exit_code = self.process.returncode
            self.worker.join(2)
            self.process.stdin.close()
            self.process.stdout.close()
            self.stderr.close()
            self.process = None
        return exit_code

    def wait_event(self, predicate, after=0, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events[after:]:
                if predicate(event):
                    return event
            if self.process.poll() is not None or self.uart.errors:
                break
            time.sleep(0.02)
        self.fail(f"Missing reader event; UART errors={self.uart.errors}; events={self.events}; "
                  f"stderr={(self.root / 'reader.stderr').read_text(errors='replace')[-3000:]}")

    def api(self, method, path, body=None, token=True):
        connection = http.client.HTTPSConnection("127.0.0.1", self.port, context=self.tls, timeout=3)
        try:
            headers = {}
            if token:
                headers["Authorization"] = "Bearer " + (self.token if token is True else token)
            if isinstance(body, dict):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif isinstance(body, bytes):
                headers["Content-Type"] = "application/gzip"
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def key(self, code):
        os.write(self.keys, b"".join(INPUT_EVENT.pack(0, 0, 1, code, value) for value in (1, 2, 0)))

    def console(self, command):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def playback_events(self):
        path = self.root / "playback.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def wait_playback_start(self, after):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            for event in self.playback_events()[after:]:
                if event["file"] == "answer-2.wav" and event["kind"] == "start":
                    return event
            time.sleep(0.02)
        self.fail("The answer player did not start")

    def test_offline_reading_tls_sync_keys_restart_and_uart_recovery(self):
        self.assertEqual(self.api("GET", "/v1/device", token=False), (401, {"code": "PAIRING_REQUIRED"}))
        self.assertEqual(self.api("GET", "/v1/books", token="wrong"), (401, {"code": "PAIRING_REQUIRED"}))
        device_id = self.api("GET", "/v1/device")[1]["deviceId"]
        self.assertEqual(self.api("POST", "/v1/storypacks", self.archive.read_bytes())[0], 200)
        self.assertTrue(self.api("POST", "/v1/storypacks", self.archive.read_bytes())[1]["alreadyInstalled"])
        scan = self.api("POST", "/v1/scans")[1]
        self.uart.tag("0004A12B")
        self.wait_event(lambda event: event.get("tagUid") == "0004A12B")
        self.assertEqual(self.api("GET", "/v1/scans/" + scan["scanId"])[1]["tagUid"], "0004A12B")
        self.assertEqual(self.api("PUT", "/v1/book-bindings/0004A12B", {**scan, "bookId": "test-book"})[0], 200)
        self.uart.tag(None)
        self.uart.tag("0004A12B")
        self.wait_event(lambda event: event.get("status") == "started")
        self.wait_event(lambda event: event.get("position", {}).get("segmentId") == "s2")
        self.configure_audio(None)
        self.uart.hold_for_polls()
        self.assertEqual(sum(event.get("status") == "started" for event in self.events), 1)
        mark = len(self.events)
        self.key(105)
        self.wait_event(lambda event: event.get("segmentId") == "s1" and "text" in event, after=mark)
        self.uart.hold_for_polls()
        self.assertEqual(sum(event.get("segmentId") == "s1" and "text" in event for event in self.events[mark:]), 1)
        mark = len(self.events)
        self.key(106)
        self.wait_event(lambda event: event.get("segmentId") == "s2" and "text" in event, after=mark)
        mark = len(self.events)
        self.console("record 2")
        self.wait_event(lambda event: event.get("segmentId") == "s2" and bool(event.get("events")), after=mark)
        recordings = [json.loads(line)["path"] for line in (self.root / "capture.jsonl").read_text().splitlines()]
        self.assertEqual(len(recordings), 1)
        self.assertFalse(Path(recordings[0]).exists(), "The actual temporary recording must be removed")
        mark = len(self.events)
        self.key(28)
        answer = self.wait_event(lambda event: event.get("status") == "answered", after=mark)
        self.assertEqual(answer["segmentId"], "s2")
        self.wait_event(lambda event: event.get("audio") == "listening", after=self.events.index(answer) + 1)
        mark = len(self.events)
        self.key(1)
        session = self.wait_event(lambda event: "sessionId" in event, after=mark)
        self.assertEqual(session["lastSegmentId"], "s2")
        self.assertEqual(session["positionSource"], "audio")
        self.assertEqual(len(session["answers"]), 1)
        self.assertEqual(session["answers"][0]["source"], "voice")
        self.assertEqual(session["answers"][0]["evidence"], "因为小兔没有带伞")
        self.assertFalse(any("unexpectedOutput" in event for event in self.events))
        trace = session["trajectory"][0]
        review_path = f"/v1/position-reviews/{session['sessionId']}/{trace['eventId']}"
        review_request = {"status": "position_mismatch", "note": "和孩子复核后发现偏差", "updatedAtMs": 2000}
        self.assertEqual(self.api("PUT", review_path, token=False),
                         (401, {"code": "PAIRING_REQUIRED"}))
        review = self.api("PUT", review_path, review_request)[1]
        self.assertEqual(review["deviceId"], device_id)
        self.assertEqual(review["status"], "position_mismatch")
        self.uart.tag(None)
        self.assertEqual(self.stop_reader(), 0)
        self.start_reader()
        self.assertEqual(self.api("GET", "/v1/device")[1]["deviceId"], device_id)
        self.assertEqual(self.api("GET", "/v1/reading-sessions?limit=8")[1]["items"], [session])
        self.assertEqual(self.api("GET", "/v1/position-reviews")[1]["items"], [review])
        self.uart.tag("0004A12B")
        self.wait_event(lambda event: event.get("status") == "started")
        self.console("select s2")
        self.wait_event(lambda event: event.get("segmentId") == "s2" and "text" in event)
        self.configure_audio(None, "foreign.wav")
        mark = len(self.events)
        self.console("record 2")
        uncertain = self.wait_event(lambda event: any(item.get("status") == "uncertain"
                                                       for item in event.get("events", [])), after=mark)
        self.assertEqual(uncertain["segmentId"], "s2")
        mark = len(self.events)
        self.console("ask-voice 2")
        confirmation = self.wait_event(lambda event: event.get("status") == "needsPositionConfirmation",
                                       after=mark)
        self.assertEqual(confirmation["segmentId"], "s2")
        self.assertTrue(confirmation["audio"].endswith("prompt-needsPositionConfirmation.wav"))
        self.configure_audio(None)
        self.console("select s2")
        self.wait_event(lambda event: event.get("segmentId") == "s2" and "text" in event)
        mark = len(self.events)
        self.console("ask 它为什么带伞？")
        wrong_position = self.wait_event(lambda event: event.get("status") == "unsupported", after=mark)
        self.assertNotIn("evidence", wrong_position)
        (self.root / "hold-answer").touch()
        mark = len(self.events)
        playback_mark = len(self.playback_events())
        self.console("ask " + self.question)
        self.wait_event(lambda event: event.get("status") == "answered", after=mark)
        playing = self.wait_playback_start(playback_mark)
        self.console("finish")
        canceled = self.wait_event(lambda event: "sessionId" in event, after=mark)
        self.assertNotIn("answers", canceled)
        self.assertFalse(any(event["pid"] == playing["pid"] and event["kind"] == "end"
                             for event in self.playback_events()))
        batch = self.api("GET", "/v1/reading-sessions?limit=8")[1]
        self.assertEqual(batch["items"], [session, canceled])
        self.assertEqual(self.api("GET", "/v1/reading-sessions?after=" + batch["nextCursor"])[1]["items"], [])
        self.uart.tag(None)
        mark = len(self.events)
        with self.uart.condition:
            self.uart.corrupt_next = True
        self.wait_event(lambda event: event.get("rfid") == "unavailable", after=mark)
        self.assertEqual(self.api("POST", "/v1/scans"), (503, {"code": "RFID_UNAVAILABLE"}))
        self.wait_event(lambda event: event.get("rfid") == "ready", after=mark)
        self.assertTrue(self.api("GET", "/v1/device")[1]["rfidAvailable"])
        self.assertEqual(self.api("GET", "/v1/reading-sessions")[1]["items"], [session, canceled])
        self.assertEqual(self.uart.errors, [])
        self.assertFalse(any("unexpectedOutput" in event for event in self.events))


if __name__ == "__main__":
    unittest.main()
