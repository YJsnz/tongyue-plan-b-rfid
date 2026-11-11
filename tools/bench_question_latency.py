"""Measure spoken-question end to audible-answer start from synchronized stereo PCM WAV."""

import argparse
import array
import json
import math
from pathlib import Path
import statistics
import sys
import wave


BLOCK_MS = 10
MIN_ACTIVITY_BLOCKS = 3


def window_frames(window, wav):
    if (not isinstance(window, list) or len(window) != 2 or
            any(type(value) not in (int, float) or not math.isfinite(value) for value in window)):
        raise ValueError("Each window must be [startMs, endMs]")
    start, end = window
    rate = wav.getframerate()
    first, last = round(start * rate / 1000), round(end * rate / 1000)
    if not 0 <= first < last <= wav.getnframes() or not 30 <= end - start <= 60_000:
        raise ValueError("Each window must span 30–60000 ms within the recording")
    return first, last


def activity(wav, window, channel, threshold):
    start, end = window_frames(window, wav)
    block = round(wav.getframerate() * BLOCK_MS / 1000)
    minimum_power = (32768 * 10 ** (threshold / 20)) ** 2
    wav.setpos(start)
    spans = []
    run_start = None
    for offset in range(start, end - block + 1, block):
        samples = array.array("h")
        samples.frombytes(wav.readframes(block))
        if len(samples) != block * 2:
            raise ValueError("WAV ends before the annotated window")
        if sys.byteorder != "little":
            samples.byteswap()
        active = sum(sample * sample for sample in samples[channel::2]) >= minimum_power * block
        if active and run_start is None:
            run_start = offset
        if not active and run_start is not None:
            if offset - run_start >= block * MIN_ACTIVITY_BLOCKS:
                spans.append((run_start, offset))
            run_start = None
    last_block_end = end - (end - start) % block
    if run_start is not None and last_block_end - run_start >= block * MIN_ACTIVITY_BLOCKS:
        spans.append((run_start, last_block_end))
    return spans, start, last_block_end


def measure(case, base, question_threshold, answer_threshold):
    if type(case.get("answerConfirmed")) is not bool:
        raise ValueError("Every case must say whether the expected answer was heard")
    recording = case["recording"]
    with wave.open(str((base / recording).resolve()), "rb") as wav:
        rate = wav.getframerate()
        if wav.getnchannels() != 2 or wav.getsampwidth() != 2 or rate not in (16000, 44100, 48000):
            raise ValueError(f"{recording}: expected stereo PCM16 WAV at 16, 44.1 or 48 kHz")
        question, _, question_limit = activity(wav, case["questionWindowMs"], 0, question_threshold)
        answer, answer_start, _ = activity(wav, case["answerWindowMs"], 1, answer_threshold)

    result = {"recording": recording, "answerConfirmed": case["answerConfirmed"]}
    if not question or not answer:
        result["status"] = "question_not_detected" if not question else "answer_not_detected"
        return result
    question_end = question[-1][1]
    answer_begin = answer[0][0]
    if question_end == question_limit or answer_begin == answer_start:
        result["status"] = "activity_touches_window_boundary"
        return result
    latency = (answer_begin - question_end) * 1000 / rate
    result.update(questionEndMs=round(question_end * 1000 / rate, 3),
                  answerStartMs=round(answer_begin * 1000 / rate, 3), latencyMs=round(latency, 3))
    result["status"] = ("answer_precedes_question_end" if latency < 0 else
                        "measured" if case["answerConfirmed"] else "answer_not_confirmed")
    return result


def run(args):
    manifest = json.loads(args.cases.read_text(encoding="utf-8-sig"))
    kind = manifest["captureKind"]
    if kind not in ("hardware", "synthetic"):
        raise ValueError("captureKind must be hardware or synthetic")
    if kind == "hardware" and (not manifest.get("boardModel") or not manifest.get("imageVersion")):
        raise ValueError("Hardware recordings must identify the board model and image version")
    cases = manifest["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("Expected at least one annotated recording")
    base = args.cases.resolve().parent
    results = []
    seen = set()
    for case in cases:
        source = (base / case["recording"]).resolve()
        if source in seen:
            raise ValueError("Use one recording per independent question trial")
        seen.add(source)
        results.append(measure(case, base, args.question_dbfs, args.answer_dbfs))
    values = sorted(case["latencyMs"] for case in results if case["status"] == "measured")
    p95 = values[math.ceil(0.95 * len(values)) - 1] if values else None
    passes = len(values) == len(results) and len(values) >= args.min_trials and p95 <= args.target_ms
    return {"captureKind": kind, "boardModel": manifest.get("boardModel"), "imageVersion": manifest.get("imageVersion"),
            "targetP95Ms": args.target_ms, "minTrials": args.min_trials, "blockMs": BLOCK_MS,
            "questionThresholdDbfs": args.question_dbfs, "answerThresholdDbfs": args.answer_dbfs,
            "trials": len(results), "confirmedMeasurements": len(values), "p95Ms": p95,
            "medianMs": statistics.median(values) if values else None, "passesLatencyTarget": passes,
            "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--target-ms", type=int, default=2000)
    parser.add_argument("--min-trials", type=int, default=20)
    parser.add_argument("--question-dbfs", type=float, default=-35)
    parser.add_argument("--answer-dbfs", type=float, default=-35)
    args = parser.parse_args()
    if (args.target_ms < 1 or args.min_trials < 1 or
            not -90 <= args.question_dbfs <= 0 or not -90 <= args.answer_dbfs <= 0):
        parser.error("Use positive targets and thresholds between -90 and 0 dBFS")
    try:
        report = run(args)
    except (KeyError, OSError, ValueError, TypeError, wave.Error) as error:
        parser.exit(2, f"Question latency: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passesLatencyTarget"] else 1


if __name__ == "__main__":
    sys.exit(main())
