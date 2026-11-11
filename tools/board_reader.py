"""Linux userspace reader with PN532 HSU, ALSA and the phone's v1 API."""

import argparse
from contextlib import contextmanager
import fcntl
import hmac
import ipaddress
import json
import os
import re
import selectors
import shutil
import ssl
import signal
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from pn532_hsu import Pn532, TapDetector
from reader_keys import watch_keys
from rfid_simulator import ApiError, Handler, Reader, StateWriteError


PCM_STALL_SECONDS = 10


def log(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


@contextmanager
def state_lock(path):
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise StateWriteError(f"Another reader is using {path}") from error
        yield
    finally:
        os.close(fd)


def stop_process(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class BoardHandler(Handler):
    def authorize(self):
        token = self.server.access_token
        if token is not None:
            supplied = self.headers.get_all("Authorization", [])
            expected = f"Bearer {token}".encode("ascii")
            if len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode("latin-1"), expected):
                raise ApiError(401, "PAIRING_REQUIRED")


class BoardServer(ThreadingHTTPServer):
    def __init__(self, address, tls, access_token):
        self.tls = tls
        self.access_token = access_token
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, BoardHandler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def finish_request(self, request, address):
        if self.tls is None:
            super().finish_request(request, address)
            return
        request.settimeout(4)
        with self.tls.wrap_socket(request, server_side=True, do_handshake_on_connect=False) as socket:
            try:
                socket.do_handshake()
            except (ssl.SSLError, TimeoutError):
                return
            super().finish_request(socket, address)


def network_options(args, parser):
    try:
        address = ipaddress.IPv4Address(args.listen)
    except ValueError:
        parser.error("--listen requires a local IPv4 address")
    options = (args.tls_cert, args.tls_key, args.pairing_token_file)
    if not address.is_loopback or any(options):
        if not all(options):
            parser.error("LAN access requires --tls-cert, --tls-key and --pairing-token-file")
        token = args.pairing_token_file.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            parser.error("Pairing token must be 32–128 URL-safe characters generated from random bytes")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.tls_cert, args.tls_key)
        return context, token
    return None, None


class BoardReader(Reader):
    def __init__(self, args, recordings):
        super().__init__(args.state, 30, args.packs.resolve(), args.locator.resolve(), recordings,
                         device_prefix="reader", trusted_clock=not args.no_trusted_clock)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.playback = None
        self.pending_answer = None
        self.stream = None
        self.stream_lines = bytearray()
        self.listen_after = 0.0
        self.manual_capture = False
        self.continuous = args.continuous
        self.listening_enabled = True
        self.capture = args.capture_device
        self.output = args.playback_device
        self.firmware = None

    def dispatch(self, method, target, data):
        if not target.path.startswith("/v1/"):
            raise ApiError(404, "NOT_FOUND")
        with self.lock:
            if method == "POST" and target.path == "/v1/scans" and self.firmware is None:
                raise ApiError(503, "RFID_UNAVAILABLE")
            result = super().dispatch(method, target, data)
            if method == "GET" and target.path == "/v1/device":
                result.update(firmwareVersion="linux-reader-1", rfidAvailable=self.firmware is not None)
            return result

    def stop_audio(self):
        if self.playback is not None:
            completed = self.playback.poll() == 0
            stop_process(self.playback)
            self.playback = None
            if completed and self.pending_answer is not None:
                session, result, source = self.pending_answer
                if self.active is session:
                    self.remember_answer(result, source)
            self.pending_answer = None

    def stop_stream(self):
        if self.stream is None:
            return
        stream = self.stream
        self.stream = None
        self.stream_lines.clear()
        self.close_pcm_locator(stream)

    @staticmethod
    def close_pcm_locator(stream):
        capture, locator = stream
        stop_process(capture)
        stop_process(locator)
        locator.stdout.close()

    def open_pcm_locator(self, locator_command, seconds=None):
        command = ["arecord", "-q", "-D", self.capture, "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", "16000"]
        if seconds is not None:
            command.extend(("-d", str(seconds)))
        capture = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        locator = None
        try:
            locator = subprocess.Popen(locator_command, stdin=capture.stdout, stdout=subprocess.PIPE)
            os.set_blocking(locator.stdout.fileno(), False)
            return capture, locator
        except (OSError, ValueError):
            stop_process(capture)
            if locator is not None:
                stop_process(locator)
                locator.stdout.close()
            raise
        finally:
            capture.stdout.close()

    def start_stream(self):
        command = self.locator_command("-")
        command.insert(3, "--stream-progress")
        self.stream = self.open_pcm_locator(command)
        self.stream_progress_at = time.monotonic()
        self.stream_progress_samples = 0

    def stream_line(self, line):
        if line.startswith(b"endMs\t"):
            return
        if line.startswith(b"pcm\t"):
            samples = int(line[4:])
            if samples <= self.stream_progress_samples:
                raise ValueError("PCM progress did not advance")
            self.stream_progress_samples = samples
            self.stream_progress_at = time.monotonic()
            log({"audio": "pcm_progress", "readerPid": os.getpid(), "streamId": self.stream[1].pid,
                 "inputSamples": samples, "monotonicMs": round(self.stream_progress_at * 1000)})
            return
        log({"position": self.apply_locator_event(line.rstrip(b"\r").decode("ascii"))})

    def listen_for_question(self, command, seconds):
        stream = self.open_pcm_locator(command, seconds)
        capture, locator = stream
        deadline = time.monotonic() + seconds + 4
        pending = bytearray()
        header = False
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(locator.stdout, selectors.EVENT_READ)
                while not self.stop.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ApiError(422, "LOCATOR_TIMEOUT")
                    if not ready.select(min(remaining, 0.2)):
                        continue
                    chunk = os.read(locator.stdout.fileno(), 4096)
                    if not chunk:
                        if locator.wait(timeout=1) or capture.wait(timeout=1) or not header:
                            raise ApiError(422, "LOCATOR_FAILED")
                        return []
                    pending.extend(chunk)
                    while b"\n" in pending:
                        line, _, pending = pending.partition(b"\n")
                        if not header:
                            if not line.startswith(b"endMs\t"):
                                raise ApiError(422, "LOCATOR_FAILED")
                            header = True
                            continue
                        if capture.poll() not in (None, 0):
                            raise ApiError(422, "LOCATOR_FAILED")
                        return [line.rstrip(b"\r").decode("ascii")]
                    if len(pending) > 8192:
                        raise ApiError(422, "LOCATOR_FAILED")
            return []
        finally:
            self.close_pcm_locator(stream)

    def watch_audio(self):
        while not self.stop.wait(0.05):
            with self.lock:
                if self.playback is not None and self.playback.poll() is not None:
                    if self.playback.returncode:
                        log({"audio": "playback_failed", "exitCode": self.playback.returncode})
                    self.stop_audio()
                    self.listen_after = time.monotonic() + 0.2
                if (not self.continuous or not self.listening_enabled or self.active is None or
                        self.playback is not None or self.manual_capture or time.monotonic() < self.listen_after):
                    continue
                try:
                    if self.stream is None:
                        self.start_stream()
                        log({"audio": "listening"})
                    capture, locator = self.stream
                    try:
                        chunk = os.read(locator.stdout.fileno(), 4096)
                    except BlockingIOError:
                        chunk = None
                    if chunk:
                        self.stream_lines.extend(chunk)
                        while b"\n" in self.stream_lines:
                            line, _, tail = self.stream_lines.partition(b"\n")
                            self.stream_lines = tail
                            self.stream_line(line)
                    if len(self.stream_lines) > 8192 or chunk == b"" or capture.poll() is not None:
                        raise OSError("Continuous capture or locator stopped")
                    if time.monotonic() - self.stream_progress_at > PCM_STALL_SECONDS:
                        raise OSError("Continuous PCM processing stalled")
                except (OSError, ValueError) as error:
                    self.stop_stream()
                    self.listen_after = time.monotonic() + 1
                    log({"audio": "capture_failed", "message": str(error)})

    def play(self, result, answer_source=None):
        if "audio" not in result:
            return
        self.stop_stream()
        self.stop_audio()
        audio = self.active["pack"].directory / result["audio"]
        self.playback = subprocess.Popen(["aplay", "-q", "-D", self.output, str(audio)], stdin=subprocess.DEVNULL)
        if answer_source is not None and result.get("status") == "answered":
            self.pending_answer = self.active, result, answer_source

    def watch_cards(self, path):
        detector = TapDetector()
        while not self.stop.is_set():
            try:
                with Pn532(path) as chip:
                    with self.lock:
                        self.firmware = chip.firmware
                    log({"rfid": "ready", "firmware": chip.firmware})
                    while not self.stop.is_set():
                        poll_started = time.monotonic()
                        uid = detector.update(chip.poll_uid())
                        if uid is not None:
                            with self.lock:
                                # A card seen in a poll already underway cannot satisfy a new scan.
                                if self.scan_open() and poll_started < self.scan["deadline"] - self.scan_timeout:
                                    continue
                                try:
                                    result = self.tap(uid)
                                    if result["status"] == "started":
                                        self.play(result)
                                    log({"uid": uid, **result})
                                except ApiError as error:
                                    log({"uid": uid, "code": error.code})
                                except StateWriteError as error:
                                    log({"uid": uid, "code": error.code, "error": str(error)})
                                except OSError as error:
                                    log({"audio": "error", "message": str(error)})
                        self.stop.wait(0.12)
            except OSError as error:
                with self.lock:
                    self.firmware = None
                log({"rfid": "unavailable", "message": str(error)})
                self.stop.wait(1)

    def record(self, seconds, *, question=False):
        if not 1 <= seconds <= 12:
            raise ValueError("record duration must be 1–12 seconds")
        with self.lock:
            if self.active is None:
                raise ApiError(409, "NO_ACTIVE_SESSION")
            if self.manual_capture:
                raise ApiError(409, "RECORDING_ACTIVE")
            if question and (self.active["positionUncertain"] or not self.active["pack"].voice_questions):
                status = "needsPositionConfirmation" if self.active["positionUncertain"] else "unsupported"
                result = self.voice_prompt(status)
                self.play(result)
                return result
            self.stop_stream()
            self.stop_audio()
            self.manual_capture = True
            session = self.active
        try:
            if question:
                pack = session["pack"]
                ready = pack.voice_prompts.get("questionReady")
                if ready:
                    subprocess.run(["aplay", "-q", "-D", self.output, str(pack.directory / ready)],
                                   stdin=subprocess.DEVNULL, timeout=8, check=True)
                    self.stop.wait(0.2)
                if self.stop.is_set():
                    return {"status": "interrupted"}
                log({"audio": "question_listening"})
                started = time.monotonic()
                events = self.listen_for_question(self.question_command("-"), seconds)
                if self.stop.is_set():
                    return {"status": "interrupted"}
                with self.lock:
                    result = self.answer_voice_events(events)
                    capture_ms = round((time.monotonic() - started) * 1000)
                    self.play(result, answer_source="voice")
                    result["captureToDecisionMs"] = capture_ms
                    if "audio" in result:
                        result["captureToPlaybackDispatchMs"] = round((time.monotonic() - started) * 1000)
                    return result
            sample = self.recordings / "read-aloud.wav"
            try:
                subprocess.run(["arecord", "-q", "-D", self.capture, "-t", "wav", "-f", "S16_LE", "-c", "1",
                                "-r", "16000", "-d", str(seconds), str(sample)],
                               stdin=subprocess.DEVNULL, timeout=seconds + 5, check=True)
                with self.lock:
                    if self.active is not session:
                        raise ApiError(409, "SESSION_CHANGED")
                    return self.locate(sample.name)
            finally:
                sample.unlink(missing_ok=True)
        finally:
            with self.lock:
                self.manual_capture = False
                if question:
                    self.listen_after = time.monotonic() + 0.2

    def control(self, command, value):
        if command == "record":
            return self.record(int(value or "5"))
        if command == "ask-voice":
            return self.record(int(value or "4"), question=True)
        with self.lock:
            if command == "listen":
                if not self.continuous:
                    raise ApiError(409, "CONTINUOUS_CAPTURE_DISABLED")
                self.listening_enabled = not self.listening_enabled
                if not self.listening_enabled:
                    self.stop_stream()
                return {"listeningEnabled": self.listening_enabled}
            if self.manual_capture and command in ("select", "next", "previous", "ask", "finish"):
                raise ApiError(409, "RECORDING_ACTIVE")
            if command in ("next", "previous"):
                if self.active is None:
                    raise ApiError(409, "NO_ACTIVE_SESSION")
                segments = self.active["pack"].segments
                index = next(i for i, segment in enumerate(segments) if segment["segmentId"] == self.active["segmentId"])
                step = 1 if command == "next" else -1
                value = segments[max(0, min(index + step, len(segments) - 1))]["segmentId"]
                command = "select"
            if command == "select":
                result = self.select_segment(value)
                self.play(result)
                return result
            if command == "ask":
                result = self.ask(value)
                self.play(result, answer_source="text")
                return result
            if command == "finish":
                self.stop_stream()
                self.stop_audio()
                return self.finish(None)
        raise ValueError("Commands: select SEGMENT, next, previous, listen, record [SECONDS], ask TEXT, ask-voice [SECONDS], finish, quit")

    def run_control(self, command, value=""):
        try:
            log(self.control(command, value))
        except ApiError as error:
            log({"code": error.code})
        except StateWriteError as error:
            log({"code": error.code, "error": str(error)})
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            log({"error": str(error)})


def probe(uart):
    with Pn532(uart) as chip:
        detector = TapDetector()
        log({"rfid": "ready", "firmware": chip.firmware})
        while True:
            uid = detector.update(chip.poll_uid())
            if uid is not None:
                log({"uid": uid})
            time.sleep(0.12)


def interrupt(_signal, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uart", required=True)
    parser.add_argument("--probe", action="store_true", help="Print UIDs without starting audio or HTTP")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--packs", type=Path)
    parser.add_argument("--locator", type=Path)
    parser.add_argument("--capture-device", default="default")
    parser.add_argument("--playback-device", default="default")
    parser.add_argument("--continuous", action="store_true", help="Locate reading from live microphone PCM")
    parser.add_argument("--keys-device", type=Path, help="Linux evdev input path for physical keys")
    parser.add_argument("--key-next", type=int)
    parser.add_argument("--key-previous", type=int)
    parser.add_argument("--key-listen", type=int)
    parser.add_argument("--key-ask", type=int, help="Record one offline spoken question")
    parser.add_argument("--key-finish", type=int)
    parser.add_argument("--no-console", action="store_true", help="Run without stdin controls")
    parser.add_argument("--no-trusted-clock", action="store_true", help="Save unknown dates when the board clock is not set")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--listen", default="127.0.0.1", help="Local IPv4 address for phone connections")
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--pairing-token-file", type=Path)
    args = parser.parse_args()
    if args.probe:
        probe(args.uart)
        return
    if not (args.state and args.packs and args.packs.is_dir() and args.locator and args.locator.is_file()):
        parser.error("--state, --packs and --locator are required outside probe mode")
    if not shutil.which("arecord") or not shutil.which("aplay"):
        parser.error("The board image must provide ALSA arecord and aplay")
    keys = [(code, command) for code, command in ((args.key_next, "next"), (args.key_previous, "previous"),
                                                (args.key_listen, "listen"), (args.key_ask, "ask-voice"),
                                                (args.key_finish, "finish"))
            if code is not None]
    mapping = dict(keys)
    if bool(args.keys_device) != bool(mapping) or len(mapping) != len(keys) or any(not 1 <= code <= 767 for code in mapping):
        parser.error("Configure --keys-device and one or more distinct EV_KEY codes (1–767) together")
    if args.key_listen is not None and not args.continuous:
        parser.error("--key-listen requires --continuous")
    tls, access_token = network_options(args, parser)
    args.state = args.state.resolve()
    ram = Path("/dev/shm")
    temp_root = ram if ram.is_dir() else None
    with state_lock(args.state), tempfile.TemporaryDirectory(prefix="tongyue-read-", dir=temp_root) as directory, \
            BoardServer((args.listen, args.port), tls, access_token) as http:
        reader = BoardReader(args, Path(directory).resolve())
        http.reader = reader
        workers = [threading.Thread(target=reader.watch_cards, args=(args.uart,)),
                   threading.Thread(target=reader.watch_audio), threading.Thread(target=http.serve_forever)]
        if mapping:
            workers.append(threading.Thread(target=watch_keys, args=(args.keys_device, mapping, reader.stop,
                reader.run_control, lambda error: log({"keys": "unavailable", "message": str(error)}))))
        for worker in workers:
            worker.start()
        scheme = "https" if tls else "http"
        log({"api": f"{scheme}://{args.listen}:{http.server_port}",
             "controls": "select, next, previous, listen, record, ask, ask-voice, finish, quit"})
        signal.signal(signal.SIGTERM, interrupt)
        try:
            if args.no_console:
                reader.stop.wait()
            else:
                for line in iter(input, "quit"):
                    command, _, value = line.strip().partition(" ")
                    if command:
                        reader.run_control(command, value.strip())
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            reader.stop.set()
            http.shutdown()
            for worker in workers:
                worker.join()
            reader.stop_stream()
            reader.stop_audio()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except OSError as error:
        raise SystemExit(f"Board reader: {error}") from error
