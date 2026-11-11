"""Verify live PCM progress, stall recovery and continuous-run evidence without a microphone."""

import json
import math
import os
import queue
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

if os.name == "posix":
    from board_acceptance import CaptureProgress, observe
    from board_reader import BoardReader
    from storypack import compile_pack, write_json


@unittest.skipUnless(os.name == "posix", "The board runtime uses Linux /proc and POSIX devices")
class ProgressEvidenceTests(unittest.TestCase):
    @staticmethod
    def event(at, samples=16000, stream=7, reader=42):
        return {"audio": "pcm_progress", "readerPid": reader, "streamId": stream,
                "inputSamples": samples, "monotonicMs": round(at * 1000)}

    def test_healthy_progress_and_unrelated_reader(self):
        progress = CaptureProgress(42, 100)
        progress.feed(self.event(101))
        progress.feed(self.event(102, 32000))
        progress.feed(self.event(103, 1, reader=999))
        progress.check(103)
        report = progress.report()
        self.assertTrue(report["ok"])
        self.assertEqual(report["samplesAdvanced"], 16000)
        self.assertEqual(report["updates"], 2)

    def test_stall_remains_failed_after_progress_resumes(self):
        progress = CaptureProgress(42, 100)
        progress.feed(self.event(101))
        progress.feed(self.event(115, 32000))
        self.assertFalse(progress.report()["ok"])
        self.assertTrue(progress.report()["stalled"])
        self.assertEqual(progress.report()["maxGapSeconds"], 14)

    def test_restart_and_counter_rollback_fail(self):
        for stream, samples in ((8, 16000), (7, 8000)):
            with self.subTest(stream=stream):
                progress = CaptureProgress(42, 100)
                progress.feed(self.event(101))
                progress.feed(self.event(102, samples, stream=stream))
                self.assertFalse(progress.report()["ok"])

    def test_processes_alive_without_pcm_cannot_pass_observation(self):
        self.assertFalse(self.run_observation(False)["ok"])

    def test_observation_accepts_fresh_pcm_but_not_as_two_hours(self):
        report = self.run_observation(True)
        self.assertTrue(report["ok"])
        self.assertFalse(report["meetsTwoHourWindow"])
        self.assertGreaterEqual(report["pcmProgress"]["updates"], 2)

    def run_observation(self, with_progress):
        clock = SimpleNamespace(now=100.0, samples=0)

        def read():
            if not with_progress:
                return ""
            clock.samples += 16000
            return json.dumps(self.event(clock.now, clock.samples)) + "\n"

        log = Mock()
        log.read.side_effect = read
        log.__enter__ = Mock(return_value=log)
        log.__exit__ = Mock(return_value=False)
        args = SimpleNamespace(pid=42, seconds=2, interval=1, log=Mock(open=Mock(return_value=log)))
        root = {"pid": 42, "name": "python3", "state": "S", "start": 1, "ticks": 0, "rssPages": 1}
        processes = [root, {**root, "pid": 43, "name": "arecord"}, {**root, "pid": 44, "name": "locate_wav"}]
        with patch("board_acceptance.process_stat", return_value=root), \
                patch("board_acceptance.process_tree", return_value=processes), \
                patch("board_acceptance.time.monotonic", side_effect=lambda: clock.now), \
                patch("board_acceptance.time.sleep", side_effect=lambda seconds: setattr(clock, "now", clock.now + seconds)):
            report = observe(args)
        self.assertTrue(report["processAlive"])
        self.assertEqual(report["continuousCaptureSamples"], report["samples"])
        return report

    def test_heartbeat_does_not_change_reading_position(self):
        reader = BoardReader.__new__(BoardReader)
        reader.stream = (None, SimpleNamespace(pid=7))
        reader.stream_progress_samples = 0
        reader.active = {"segmentId": "s2", "positionSource": "manual", "positionUncertain": False}
        with patch("board_reader.log") as logged:
            reader.stream_line(b"pcm\t16000")
        self.assertEqual(reader.active, {"segmentId": "s2", "positionSource": "manual", "positionUncertain": False})
        self.assertEqual(logged.call_args.args[0]["inputSamples"], 16000)
        with self.assertRaises(ValueError):
            reader.stream_line(b"pcm\t16000")

    def test_stalled_live_processes_trigger_capture_recovery(self):
        reader = BoardReader.__new__(BoardReader)
        reader.stop = Mock()
        reader.stop.wait.side_effect = [False, True]
        reader.lock = threading.Lock()
        reader.playback = None
        reader.continuous = reader.listening_enabled = True
        reader.manual_capture = False
        reader.listen_after = 0
        reader.active = {"segmentId": "s2"}
        reader.stream = (Mock(poll=Mock(return_value=None)), Mock(stdout=Mock(fileno=Mock(return_value=10))))
        reader.stream_lines = bytearray()
        reader.stream_progress_at = 80
        reader.stop_stream = Mock()
        with patch("board_reader.time.monotonic", return_value=100), \
                patch("board_reader.os.read", side_effect=BlockingIOError), patch("board_reader.log") as logged:
            reader.watch_audio()
        reader.stop_stream.assert_called_once()
        self.assertEqual(logged.call_args.args[0]["audio"], "capture_failed")
        self.assertIn("stalled", logged.call_args.args[0]["message"])
        self.assertEqual(reader.active["segmentId"], "s2")


class NativePcmProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.command = shlex.split(os.environ.get("TONGYUE_LOCATOR_COMMAND", ""))
        if not cls.command:
            raise unittest.SkipTest("Set TONGYUE_LOCATOR_COMMAND to a native or QEMU locate_wav v3 command")
        cls.command[0] = str(Path(shutil.which(cls.command[0]) or cls.command[0]).resolve())
        if Path(cls.command[-1]).is_file():
            cls.command[-1] = str(Path(cls.command[-1]).resolve())
        cls.workspace = tempfile.TemporaryDirectory(prefix="tongyue-pcm-test-")
        root = Path(cls.workspace.name)
        cls.wav = root / "reference.wav"
        cls.speech = b"".join(struct.pack("<h", round(3000 * math.sin(i * math.tau * 440 / 16000)))
                               for i in range(16000))
        with wave.open(str(cls.wav), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(cls.speech)
        cls.bank = root / "reference.mfcc"
        subprocess.run([*cls.command, "--compile-references", str(cls.bank), "s1=" + str(cls.wav)],
                       capture_output=True, timeout=10, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def test_silence_progress_arrives_before_eof(self):
        process = subprocess.Popen([*self.command, "-", "--stream-progress", "--bank=" + str(self.bank)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        received = queue.Queue()

        def read_lines():
            for line in process.stdout:
                received.put(line)
            received.put(None)

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        try:
            process.stdin.write(b"\0" * 64000)
            process.stdin.flush()
            output = bytearray()
            deadline = time.monotonic() + 5
            while b"pcm\t32000\n" not in output and time.monotonic() < deadline:
                try:
                    line = received.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    break
                output.extend(line.replace(b"\r\n", b"\n"))
            self.assertIsNone(process.poll(), "The stream must still be open when progress arrives")
            self.assertIn(b"pcm\t16000\n", output)
            self.assertIn(b"pcm\t32000\n", output)
            self.assertEqual(len(output.splitlines()), 3, "Silence must not emit reading position events")
            process.stdin.close()
            process.stdin = None
            process.wait(timeout=5)
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stdin is not None:
                process.stdin.close()
            reader.join(5)
            process.stdout.close()
            process.stderr.close()

    def test_progress_opt_in_preserves_position_events(self):
        pcm = self.speech + b"\0" * 25600
        results = []
        for flags in ([], ["--stream-progress"]):
            result = subprocess.run([*self.command, "-", *flags, "--bank=" + str(self.bank)], input=pcm,
                                    capture_output=True, timeout=10, check=True)
            lines = result.stdout.splitlines()
            results.append([line for line in lines if not line.startswith(b"pcm\t")])
            self.assertEqual(any(line.startswith(b"pcm\t") for line in lines), bool(flags))
        self.assertEqual(results[0], results[1])
        self.assertIn(b"\tmatched\ts1\t", results[0][1])

    def test_partial_pcm_sample_is_rejected(self):
        result = subprocess.run([*self.command, "-", "--stream-progress", "--bank=" + str(self.bank)], input=b"\0",
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Incomplete PCM16 sample", result.stderr)

    @unittest.skipUnless(os.name == "posix", "The board runtime needs POSIX processes")
    def test_real_board_pipe_recovers_when_live_capture_stops_writing(self):
        with tempfile.TemporaryDirectory(prefix="tongyue-pipe-test-") as folder:
            root = Path(folder)
            (root / "reference.wav").write_bytes(self.wav.read_bytes())
            write_json(root / "book.json", {"bookId": "test-book", "version": "test-1", "title": "测试绘本",
                "language": "zh-CN", "rights": "原创测试声波", "segments": [
                    {"segmentId": "s1", "text": "用于验证数据流。", "audio": "reference.wav"}]})
            compile_pack(root / "book.json", root / "packs")
            programs = root / "bin"
            programs.mkdir()
            capture = programs / "arecord"
            capture.write_text(f"#!{sys.executable}\nimport sys, time\n"
                               "for _ in range(4):\n    sys.stdout.buffer.write(b'\\0' * 32000)\n"
                               "    sys.stdout.buffer.flush()\n    time.sleep(0.05)\n"
                               "time.sleep(60)\n", encoding="utf-8")
            capture.chmod(0o755)
            locator = programs / "locate_wav"
            locator.write_text("#!/bin/sh\nexec " + shlex.join(self.command) + ' "$@"\n', encoding="utf-8")
            locator.chmod(0o755)
            args = SimpleNamespace(state=root / "state.json", packs=root / "packs", locator=locator,
                                   no_trusted_clock=False, continuous=True, capture_device="default", playback_device="default")
            reader = BoardReader(args, root)
            reader.save({**reader.state, "bindings": {"0004A12B": "test-book"}})
            reader.tap("0004A12B")
            events, failed = [], threading.Event()

            def logged(event):
                events.append(event)
                if event.get("audio") == "capture_failed":
                    failed.set()

            with patch.dict(os.environ, {"PATH": str(programs) + os.pathsep + os.environ["PATH"]}), \
                    patch("board_reader.PCM_STALL_SECONDS", 1.0), patch("board_reader.log", side_effect=logged):
                worker = threading.Thread(target=reader.watch_audio)
                worker.start()
                try:
                    self.assertTrue(failed.wait(5), "A live but stalled capture must trigger recovery")
                finally:
                    reader.stop.set()
                    worker.join(5)
                    reader.stop_stream()
            self.assertFalse(worker.is_alive())
            progress = [event for event in events if event.get("audio") == "pcm_progress"]
            self.assertGreaterEqual(len(progress), 2)
            self.assertGreater(progress[-1]["inputSamples"], progress[0]["inputSamples"])
            self.assertIn("stalled", next(event["message"] for event in events if event.get("audio") == "capture_failed"))
            self.assertEqual(reader.active["segmentId"], "s1")
            self.assertFalse(reader.active["positionUncertain"])


if __name__ == "__main__":
    unittest.main()
