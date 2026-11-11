"""Run MFCC or optional streaming CTC against a StoryPack; emit shared JSONL logs."""

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

from ctc_alignment import CtcBookTracker, event
from storypack import StoryPack


def mfcc_events(pack, recording, command):
    result = subprocess.run([*command, str(recording.resolve()), *pack.reference_args("segments")],
                            capture_output=True, text=True, timeout=180, check=True)
    lines = result.stdout.splitlines()
    if not lines or lines[0] != "endMs\tstatus\tsegmentId\tdistance\tmargin":
        raise ValueError("Unexpected MFCC locator log")
    for line in lines[1:]:
        timestamp, status, segment, distance, margin = line.split("\t")
        if status not in ("matched", "repeated", "relocated", "uncertain"):
            raise ValueError("Invalid MFCC locator status")
        yield event("mfcc", status, segment or None, None, float(timestamp) / 1000,
                    final=True, distance=float(distance) if distance else None,
                    margin=float(margin) if margin else None)


def ctc_events(pack, samples, acoustic, tracker=None):
    import numpy as np
    tracker = tracker if tracker is not None else CtcBookTracker(pack.text_index(), acoustic)
    stream = acoustic.new_stream()
    pending, leading, pending_start, frame_index, trailing = [], [], 0, 0, 0
    duration, last_partial = len(samples) / 16000, None
    blank = acoustic.blank_id
    for offset in range(0, len(samples), 1600):
        final = offset + 1600 >= len(samples)
        chunk = stream.accept(samples[offset:offset + 1600], final=final)
        for scores in chunk:
            if frame_index * acoustic.frame_seconds >= duration:
                break
            token = int(scores.argmax())
            if not pending:
                if token == blank:
                    leading = (leading + [scores])[-2:]
                    frame_index += 1
                    continue
                pending_start = frame_index - len(leading)
                pending, leading = leading, []
            pending.append(scores)
            trailing = trailing + 1 if token == blank else 0
            frame_index += 1
            if trailing >= 17 or len(pending) >= 200:
                start = pending_start * acoustic.frame_seconds
                if len(pending) >= 200 and trailing < 17:
                    yield event("ctc", "uncertain", tracker.position[0] if tracker.position else None,
                                start, min(frame_index * acoustic.frame_seconds, duration),
                                reason="utterance_too_long", final=True)
                else:
                    yield tracker.locate(np.stack(pending), start, end_seconds=duration)
                pending, trailing, last_partial = [], 0, None
        if pending and len(chunk):
            found = tracker.locate(np.stack(pending), pending_start * acoustic.frame_seconds,
                                   final=False, end_seconds=duration)
            key = (found["status"], found["segmentId"], found.get("wordIndex"), found["reason"])
            if key != last_partial:
                yield found
                last_partial = key
    if pending:
        yield tracker.locate(np.stack(pending), pending_start * acoustic.frame_seconds, end_seconds=duration)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs", required=True, type=Path)
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--backend", required=True, choices=("mfcc", "ctc"))
    parser.add_argument("--locator", type=Path, help="Host-native MFCC executable")
    parser.add_argument("--runner", help="Optional executable prefix, e.g. qemu-arm-static")
    parser.add_argument("--runner-arg", action="append", default=[])
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--output", type=Path, help="JSONL output; default stdout")
    parser.add_argument("recording", type=Path)
    args = parser.parse_args()
    try:
        started = time.monotonic()
        pack = StoryPack(args.packs, args.book_id)
        if args.backend == "ctc":
            from ctc_acoustic import ZipformerCtc, read_pcm_wav
            if args.model_dir is None:
                raise ValueError("CTC requires --model-dir")
            samples = read_pcm_wav(args.recording)
            acoustic = ZipformerCtc(args.model_dir)
            events = ctc_events(pack, samples, acoustic)
        else:
            if args.locator is None:
                raise ValueError("MFCC requires --locator")
            command = [str(args.locator.resolve())]
            if args.runner:
                command = [args.runner, *args.runner_arg, *command]
            events = mfcc_events(pack, args.recording, command)
        output = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
        try:
            for found in events:
                if not math.isfinite(found["endSeconds"]):
                    raise ValueError("Invalid reading timestamp")
                output.write(json.dumps(found, ensure_ascii=False, allow_nan=False) + "\n")
                output.flush()
        finally:
            if args.output:
                output.close()
        print(f"PC probe elapsed seconds: {time.monotonic() - started:.3f}", file=sys.stderr)
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Reading probe: {error}\n")


if __name__ == "__main__":
    main()
