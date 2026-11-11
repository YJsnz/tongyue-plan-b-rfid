"""Measure locator accuracy, process wall time and peak RSS on labelled Linux WAV fixtures."""

import argparse
import json
import math
import platform
import resource
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path

from rfid_simulator import Reader
from storypack import StoryPack, read_json


def feature_metrics(stderr, seconds):
    prefix = "Feature profile: "
    line = next((line for line in stderr.splitlines() if line.startswith(prefix)), None)
    if line is None:
        raise ValueError("Locator did not emit a feature profile")
    samples, expected, analyzed, mfcc, vad_ns, mfcc_ns = map(int, line[len(prefix):].split())
    return {"inputSamples": samples, "expectedWindows": expected, "analysisWindows": analyzed,
            "mfccWindows": mfcc, "vadMs": vad_ns / 1_000_000, "mfccMs": mfcc_ns / 1_000_000,
            "featureRtf": (vad_ns + mfcc_ns) / 1_000_000_000 / seconds,
            "inputWindowsComplete": samples == round(seconds * 16000) and analyzed == expected}


def measure_positions(checks, events, seconds, segment_ids):
    if not isinstance(checks, list):
        raise ValueError("positionChecks must be a list of [atMs, segmentId] pairs")
    results = []
    previous = -1
    for check in checks:
        if not isinstance(check, list) or len(check) != 2:
            raise ValueError("positionChecks must be a list of [atMs, segmentId] pairs")
        timestamp, expected = check
        if (type(timestamp) not in (int, float) or not 0 <= timestamp <= seconds * 1000 or timestamp <= previous
                or expected not in segment_ids):
            raise ValueError("Position check time or segment is outside the recording or book")
        previous = timestamp
        event = next((item for item in reversed(events) if item["endMs"] <= timestamp), None)
        accepted = event is not None and event["status"] in ("matched", "repeated", "relocated")
        observed = event["segmentId"] if accepted else None
        results.append({"atMs": timestamp, "expectedSegmentId": expected, "observedSegmentId": observed,
                        "status": event["status"] if event else "no_position", "correct": observed == expected})
    return results


def recording_duration(recording):
    with wave.open(str(recording), "rb") as wav:
        if (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) != (16000, 2, 1) or not wav.getnframes():
            raise ValueError(f"Expected nonempty 16 kHz mono PCM16 recording: {recording}")
        return wav.getnframes() / wav.getframerate()


def run_cases(args):
    pack = StoryPack(args.packs, args.book_id)
    cases = read_json(args.cases)
    if not isinstance(cases, list) or not cases:
        raise ValueError("Expected a nonempty list of benchmark cases")
    references = pack.reference_args("segments", wav_only=args.wav_references)
    segment_ids = {segment["segmentId"] for segment in pack.segments}
    command = [str(args.locator.resolve())]
    if args.runner:
        command = [args.runner, *args.runner_arg, *command]
    results = []
    recordings = set()
    for case in cases:
        source = (args.cases.resolve().parent / case["recording"]).resolve()
        if args.acceptance_gates and source in recordings:
            raise ValueError("Acceptance gates require separate recordings for each case")
        recordings.add(source)
        expected = case["expected"]
        if not isinstance(expected, list) or any(not isinstance(event, list) or len(event) != 2 for event in expected):
            raise ValueError("expected must be a list of [status, segmentId] pairs")
        seconds = recording_duration(source)
        start = time.monotonic()
        options = ["--profile-features"] if args.acceptance_gates else []
        completed = subprocess.run([*command, str(source), *options, *references], capture_output=True, text=True,
                                   timeout=max(30, seconds * 10), check=False)
        elapsed = time.monotonic() - start
        lines = completed.stdout.splitlines()
        if completed.returncode or not lines or not lines[0].startswith("endMs\t"):
            raise ValueError(f"Locator failed for {source.name}: {completed.stderr.strip()}")
        prefix = "Reference preparation ms: "
        preparation = next((float(line[len(prefix):]) for line in completed.stderr.splitlines()
                            if line.startswith(prefix)), None)
        events = [Reader.parse_locator_event(line) for line in lines[1:]]
        actual = [[event["status"], event["segmentId"]] for event in events]
        positions = measure_positions(case.get("positionChecks", []), events, seconds, segment_ids)
        result = {"recording": case["recording"], "audioSeconds": seconds, "elapsedSeconds": elapsed,
                  "rtf": elapsed / seconds, "referencePreparationMs": preparation, "expected": expected,
                  "actual": actual, "events": events, "positionChecks": positions, "passed": actual == expected}
        if args.acceptance_gates:
            result["features"] = feature_metrics(completed.stderr, seconds)
        results.append(result)
    metrics = sorted(case["rtf"] for case in results)
    preparation = [case["referencePreparationMs"] for case in results if case["referencePreparationMs"] is not None]
    labelled = [check for case in results for check in case["positionChecks"]]
    correct = sum(check["correct"] for check in labelled)
    accuracy = correct / len(labelled) if labelled else None
    report = {"machine": platform.machine(), "kernel": platform.release(), "runner": args.runner,
              "bookId": args.book_id, "referenceInput": "mfcc" if references[0].startswith("--bank=") else "wav",
              "casesPassed": sum(case["passed"] for case in results),
              "casesTotal": len(results), "p95Rtf": metrics[math.ceil(0.95 * len(metrics)) - 1],
              "medianReferencePreparationMs": statistics.median(preparation) if preparation else None,
              "peakChildRssKiB": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
              "positionChecks": len(labelled), "positionChecksCorrect": correct,
              "positionAccuracy": accuracy, "cases": results}
    if args.acceptance_gates:
        features = [case["features"] for case in results]
        max_rtf = max(profile["featureRtf"] for profile in features)
        complete = all(profile["inputWindowsComplete"] for profile in features)
        mfcc = sum(profile["mfccWindows"] for profile in features)
        report["gates"] = {
            "G1": {"passed": complete and mfcc > 0 and max_rtf < 0.5, "maxFeatureRtf": max_rtf,
                   "targetExclusiveRtf": 0.5, "inputWindowsComplete": complete, "mfccWindows": mfcc},
            "G2": {"passed": len(labelled) >= args.min_position_checks and accuracy >= 0.85,
                   "minChecks": args.min_position_checks, "targetAccuracy": 0.85},
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locator", type=Path, required=True)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--runner", help="Optional emulator, such as qemu-arm-static")
    parser.add_argument("--runner-arg", action="append", default=[])
    parser.add_argument("--wav-references", action="store_true", help="Rebuild references from WAV even when a bank is available")
    parser.add_argument("--acceptance-gates", action="store_true", help="Measure G1 features and require G2 position labels")
    parser.add_argument("--min-position-checks", type=int, default=10)
    args = parser.parse_args()
    if args.runner_arg and not args.runner:
        parser.error("--runner-arg requires --runner")
    if args.min_position_checks < 1:
        parser.error("--min-position-checks must be positive")
    try:
        result = run_cases(args)
    except (OSError, KeyError, ValueError, TypeError, wave.Error, subprocess.SubprocessError) as error:
        parser.exit(2, f"Locator benchmark: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    passed = result["casesPassed"] == result["casesTotal"]
    passed = passed and all(gate["passed"] for gate in result.get("gates", {}).values())
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
