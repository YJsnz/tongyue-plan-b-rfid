"""Record Linux reader peripheral checks and continuous-run evidence as JSON."""

import argparse
import array
from collections import Counter
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

from pn532_hsu import Pn532, TapDetector


def board_info():
    model = None
    path = Path("/proc/device-tree/model")
    if path.is_file():
        model = path.read_bytes().rstrip(b"\0").decode("utf-8", errors="replace")
    return {"machine": platform.machine(), "kernel": platform.release(), "boardModel": model}


def instruct(message):
    print(message, file=sys.stderr, flush=True)


def verify_rfid(path, uid_a, uid_b, timeout):
    detector = TapDetector()
    events = []

    with Pn532(path) as chip:
        def wait_for(expected, message):
            instruct(message)
            deadline = time.monotonic() + timeout
            empty = 0
            while time.monotonic() < deadline:
                uid = chip.poll_uid()
                tap = detector.update(uid)
                empty = empty + 1 if uid is None else 0
                if expected is None and empty == 2:
                    return
                if tap is not None:
                    if expected is not None and tap != expected:
                        raise ValueError(f"Expected {expected}, received {tap}")
                    if expected is not None:
                        events.append(tap)
                        return
                time.sleep(0.12)
            raise TimeoutError(f"Timed out waiting for {expected or 'an empty reader'}")

        wait_for(None, "Remove all tags from the reader.")
        wait_for(uid_a, "Tap tag A and hold it in place for two seconds.")
        start = time.monotonic()
        held_samples = 0
        while time.monotonic() - start < 2:
            uid = chip.poll_uid()
            if uid != uid_a or detector.update(uid) is not None:
                raise ValueError("Tag A moved or produced an unexpected tap during the hold")
            held_samples += 1
            time.sleep(0.12)
        wait_for(None, "Remove tag A.")
        wait_for(uid_a, "Tap tag A again, then wait for the next instruction.")
        wait_for(None, "Remove tag A again.")
        wait_for(uid_b, "Tap tag B.")
        return {"ok": True, "firmware": chip.firmware, "uidEvents": events, "heldSamples": held_samples}


def verify_audio(capture, playback, seconds, min_rms_dbfs):
    ram = Path("/dev/shm")
    with tempfile.TemporaryDirectory(prefix="tongyue-audio-check-", dir=ram if ram.is_dir() else None) as folder:
        wav_path = Path(folder) / "g0.wav"
        instruct(f"Speak a short test phrase for {seconds} seconds now.")
        subprocess.run(["arecord", "-q", "-D", capture, "-t", "wav", "-f", "S16_LE", "-c", "1",
                        "-r", "16000", "-d", str(seconds), str(wav_path)], stdout=sys.stderr,
                       timeout=seconds + 5, check=True)
        with wave.open(str(wav_path), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise ValueError("Capture was not 16 kHz mono PCM16")
            samples = array.array("h")
            samples.frombytes(wav.readframes(wav.getnframes()))
            if sys.byteorder != "little":
                samples.byteswap()
        if len(samples) != seconds * 16000:
            raise ValueError(f"Expected {seconds * 16000} samples, captured {len(samples)}")
        rms = math.sqrt(sum(value * value for value in samples) / len(samples))
        dbfs = 20 * math.log10(rms / 32768) if rms else None
        clipping = sum(value <= -32768 or value >= 32767 for value in samples) / len(samples)
        instruct("Listen to playback. Enter yes only if you hear the spoken test phrase clearly.")
        subprocess.run(["aplay", "-q", "-D", playback, str(wav_path)], stdout=sys.stderr,
                       timeout=seconds + 5, check=True)
        heard = sys.stdin.readline().strip().lower() == "yes"
        return {"ok": heard and dbfs is not None and dbfs >= min_rms_dbfs and clipping < 0.01,
                "seconds": seconds, "frames": len(samples), "rmsDbfs": dbfs,
                "minRmsDbfs": min_rms_dbfs, "clippedFraction": clipping, "phraseConfirmed": heard}


def peripherals(args):
    checks = {}
    actions = {"rfid": lambda: verify_rfid(args.uart, args.uid_a, args.uid_b, args.tag_timeout),
               "audio": lambda: verify_audio(args.capture_device, args.playback_device, args.record_seconds,
                                              args.min_rms_dbfs)}
    for name, action in actions.items():
        try:
            checks[name] = action()
        except (OSError, ValueError, wave.Error, subprocess.SubprocessError) as error:
            instruct(f"{name}: {error}")
            checks[name] = {"ok": False, "error": str(error)}
    return {"checks": checks, "ok": all(check["ok"] for check in checks.values())}


def process_stat(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text()
    name_start, name_end = raw.index("("), raw.rindex(")")
    fields = raw[name_end + 2:].split()
    return {"pid": pid, "name": raw[name_start + 1:name_end], "state": fields[0],
            "start": int(fields[19]), "ticks": int(fields[11]) + int(fields[12]), "rssPages": int(fields[21])}


def process_tree(root):
    processes = [root]
    seen = {root["pid"]}
    for parent in processes:
        task = Path("/proc") / str(parent["pid"]) / "task"
        for children in task.glob("*/children"):
            try:
                pids = children.read_text().split()
            except FileNotFoundError:
                continue
            for child in map(int, pids):
                if child in seen:
                    continue
                seen.add(child)
                try:
                    item = process_stat(child)
                except FileNotFoundError:
                    continue
                if item["state"] != "Z":
                    processes.append(item)
    return processes


class CaptureProgress:
    """Check progress from the target reader, including gaps hidden between /proc samples."""

    def __init__(self, pid, beginning, stall_seconds=10):
        self.pid, self.beginning, self.stall_seconds = pid, beginning, stall_seconds
        self.last_at = beginning
        self.stream = None
        self.samples = self.updates = self.advanced = 0
        self.max_gap = 0.0
        self.invalid = self.restarted = self.stalled = False

    def check(self, now):
        self.max_gap = max(self.max_gap, now - self.last_at)
        self.stalled |= self.max_gap > self.stall_seconds

    def feed(self, event):
        if event.get("audio") != "pcm_progress" or event.get("readerPid") != self.pid:
            return
        stream, samples, at_ms = (event.get(key) for key in ("streamId", "inputSamples", "monotonicMs"))
        if any(type(value) is not int or value <= 0 for value in (stream, samples, at_ms)):
            self.invalid = True
            return
        at = at_ms / 1000
        if at < self.beginning:
            return
        if at < self.last_at or (self.stream == stream and samples <= self.samples):
            self.invalid = True
            return
        self.check(at)
        if self.stream is not None and self.stream != stream:
            self.restarted = True
        if self.stream == stream:
            self.advanced += samples - self.samples
        self.stream, self.samples, self.last_at = stream, samples, at
        self.updates += 1

    def report(self):
        return {"ok": self.updates >= 2 and not (self.invalid or self.restarted or self.stalled),
                "updates": self.updates, "samplesAdvanced": self.advanced,
                "maxGapSeconds": self.max_gap, "stallLimitSeconds": self.stall_seconds,
                "invalidProgress": self.invalid, "captureRestarted": self.restarted, "stalled": self.stalled}


def count_log_events(lines, counts, progress=None):
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            lower = line.lower()
            for word in ("overrun", "underrun", "out of memory", "oom-kill"):
                if word in lower:
                    counts[word] += 1
            continue
        if not isinstance(event, dict):
            continue
        if progress is not None:
            progress.feed(event)
        if event.get("audio") in ("capture_failed", "playback_failed", "error"):
            counts[event["audio"]] += 1
        if event.get("rfid") == "unavailable":
            counts["rfid_unavailable"] += 1


def observe(args):
    pid = args.pid
    original = process_stat(pid)
    page_kib = os.sysconf("SC_PAGE_SIZE") / 1024
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    beginning = time.monotonic()
    progress = CaptureProgress(pid, beginning)
    counts = Counter()
    samples = capture_samples = 0
    peak_rss = cpu_seconds = 0.0
    previous_ticks = {}
    process_alive = True
    interrupted = False
    trailing = ""
    with args.log.open(encoding="utf-8", errors="replace") as log:
        log.seek(0, os.SEEK_END)
        try:
            while True:
                chunk = log.read()
                parts = (trailing + chunk).split("\n")
                trailing = parts.pop()
                count_log_events(parts, counts, progress)
                progress.check(time.monotonic())
                try:
                    root = process_stat(pid)
                except FileNotFoundError:
                    process_alive = False
                    break
                if root["start"] != original["start"] or root["state"] == "Z":
                    process_alive = False
                    break
                processes = process_tree(root)
                samples += 1
                names = {item["name"] for item in processes}
                capture_samples += "arecord" in names and "locate_wav" in names
                peak_rss = max(peak_rss, sum(item["rssPages"] for item in processes) * page_kib)
                ticks = {(item["pid"], item["start"]): item["ticks"] for item in processes}
                cpu_seconds += sum(max(0, value - previous_ticks[key]) for key, value in ticks.items()
                                   if key in previous_ticks) / ticks_per_second
                previous_ticks = ticks
                remaining = args.seconds - (time.monotonic() - beginning)
                if remaining <= 0:
                    break
                time.sleep(min(args.interval, remaining))
        except KeyboardInterrupt:
            interrupted = True
        count_log_events((trailing + log.read()).splitlines(), counts, progress)
    elapsed = time.monotonic() - beginning
    progress.check(time.monotonic())
    pcm = progress.report()
    complete = elapsed >= args.seconds and process_alive and not interrupted
    continuous = samples > 1 and capture_samples == samples
    return {"ok": complete and continuous and pcm["ok"] and not counts, "pid": pid, "elapsedSeconds": elapsed,
            "requestedSeconds": args.seconds, "meetsTwoHourWindow": complete and args.seconds >= 7200,
            "processAlive": process_alive, "interrupted": interrupted, "samples": samples,
            "continuousCaptureSamples": capture_samples, "peakCombinedSampleRssKiB": peak_rss,
            "sampledCpuPercentOfOneCore": cpu_seconds * 100 / elapsed if elapsed else None,
            "loggedErrors": dict(counts), "pcmProgress": pcm}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    hardware = commands.add_parser("peripherals", help="Interactive UID and microphone/speaker loopback check")
    hardware.add_argument("--uart", required=True)
    hardware.add_argument("--uid-a", required=True, type=str.upper)
    hardware.add_argument("--uid-b", required=True, type=str.upper)
    hardware.add_argument("--tag-timeout", type=int, default=20)
    hardware.add_argument("--capture-device", default="default")
    hardware.add_argument("--playback-device", default="default")
    hardware.add_argument("--record-seconds", type=int, default=3)
    hardware.add_argument("--min-rms-dbfs", type=float, default=-45)
    monitor = commands.add_parser("observe", help="Monitor an already-running continuous reader session")
    monitor.add_argument("--pid", type=int, required=True)
    monitor.add_argument("--log", type=Path, required=True)
    monitor.add_argument("--seconds", type=int, default=7200)
    monitor.add_argument("--interval", type=float, default=2)
    args = parser.parse_args()
    if args.command == "peripherals":
        if (args.uid_a == args.uid_b or not all(re.fullmatch(r"(?:[0-9A-F]{8}|[0-9A-F]{14}|[0-9A-F]{20})", uid)
                for uid in (args.uid_a, args.uid_b)) or args.tag_timeout < 1 or not 1 <= args.record_seconds <= 12
                or not -90 <= args.min_rms_dbfs <= 0):
            parser.error("Provide two distinct full Type A UIDs and valid timing/audio thresholds")
        report = peripherals(args)
    else:
        if args.pid <= 0 or args.seconds < 1 or not 0.1 <= args.interval <= args.seconds:
            parser.error("Provide a live reader PID and a valid observation interval")
        try:
            report = observe(args)
        except (OSError, ValueError) as error:
            parser.exit(2, f"Observation failed: {error}\n")
    print(json.dumps({"check": args.command, **board_info(), **report}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
