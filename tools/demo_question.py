"""Run one evidence-grounded PC question and optionally synthesize/play its answer."""

import argparse
import json
from pathlib import Path

from local_tts import play, synthesize
from storypack import StoryPack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--segment-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--audio-dir", type=Path, default=Path("output/local-qa-audio"))
    parser.add_argument("--synthesize", action="store_true", help="Write an offline Chinese WAV answer")
    parser.add_argument("--play", action="store_true", help="Synthesize and play the answer locally")
    args = parser.parse_args()

    pack = StoryPack(args.packs, args.book_id)
    result = pack.answer_free(args.segment_id, args.question)
    if result.get("status") == "answered" and (args.synthesize or args.play):
        speech = synthesize(result["answer"], args.audio_dir)
        result = {**result, "synthesizedAudio": speech["path"], "speechVoice": speech["voice"],
                  "speechDurationMs": speech["durationMs"]}
        if args.play:
            play(speech["path"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "answered" else 2


if __name__ == "__main__":
    raise SystemExit(main())
