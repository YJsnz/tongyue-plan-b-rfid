"""Inspect synthetic/authorized PC CTC recordings and save reproducible evidence."""

import argparse
import json
import platform
import time
from pathlib import Path

from ctc_acoustic import MODEL_HASHES, MODEL_REPO, MODEL_REVISION, ZipformerCtc, read_pcm_wav
from ctc_alignment import greedy_ids
from reading_probe import ctc_events
from storypack import StoryPack


def benchmark(model_dir, pack, recordings):
    import numpy as np
    started = time.monotonic()
    acoustic = ZipformerCtc(model_dir)
    load_seconds = time.monotonic() - started
    cases = []
    for path in recordings:
        samples = read_pcm_wav(path)
        stream, chunks = acoustic.new_stream(), []
        started = time.monotonic()
        for offset in range(0, len(samples), 1600):
            chunks.append(stream.accept(samples[offset:offset + 1600], final=offset + 1600 >= len(samples)))
        scores = np.concatenate(chunks)[:int(np.ceil(len(samples) / 640))]
        inference_seconds = time.monotonic() - started
        started = time.monotonic()
        events = list(ctc_events(pack, samples, acoustic))
        cases.append({"recording": str(path), "audioSeconds": len(samples) / 16000,
                      "transcript": acoustic.decode_ids(greedy_ids(scores)),
                      "inferenceSeconds": inference_seconds, "inferenceRtf": inference_seconds / (len(samples) / 16000),
                      "probeSeconds": time.monotonic() - started, "events": events})
    return {"scope": "PC model and recording diagnostics; not child accuracy or Hi3516 acceptance",
            "machine": platform.machine(), "modelRepo": MODEL_REPO, "modelRevision": MODEL_REVISION,
            "modelSha256": MODEL_HASHES, "loadSeconds": load_seconds, "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("recordings", nargs="+", type=Path)
    args = parser.parse_args()
    report = benchmark(args.model_dir, StoryPack(args.packs, args.book_id), args.recordings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "cases": [{"transcript": item["transcript"],
                     "inferenceRtf": item["inferenceRtf"], "final": [(e["status"], e["segmentId"], e["reason"])
                     for e in item["events"] if e["final"]]} for item in report["cases"]]}, ensure_ascii=False))
