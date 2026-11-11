#!/bin/sh
# Build and package the Linux/ARM reader runtime and deployment instructions.
set -eu

if [ "$#" -ne 1 ]; then
    printf 'Usage: sh tools/package_reader.sh OUTPUT.tar.gz\n' >&2
    exit 2
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output=$1
mkdir -p -- "$(dirname -- "$output")"
stage=$(mktemp -d)
temporary=$(mktemp "${output}.XXXXXX")
trap 'rm -rf -- "$stage"; rm -f -- "$temporary"' EXIT
trap 'exit 1' HUP INT TERM
bundle="$stage/tongyue-reader"
mkdir -p "$bundle/tools/examples" "$bundle/docs" "$bundle/reader-build"
sh "$root/tools/build_reader_arm.sh" "$bundle/reader-build/locate_wav"
for file in board_reader.py board_preflight.py board_acceptance.py bench_locator.py bench_question_latency.py pn532_hsu.py reader_keys.py rfid_simulator.py reading_trace.py free_question.py storypack.py storypack_import.py text_index.py requirements-authoring.txt test_storypack_import.py test_reader_progress.py test_board_runtime.py test_text_index.py test_reading_trace.py test_free_question.py test_question_latency.py ctc_acoustic.py ctc_alignment.py reading_probe.py fetch_ctc_model.py bench_ctc_pc.py requirements-ctc-pc.txt test_ctc_alignment.py test_ctc_model.py; do
    cp "$root/tools/$file" "$bundle/tools/$file"
done
cp "$root/tools/examples/bear-friends.json" "$bundle/tools/examples/"
for file in RFID_MVP.md STORYPACK_FORMAT.md PLAN_B_STATUS.md CTC_PC.md; do
    cp "$root/docs/$file" "$bundle/docs/$file"
done
tar -C "$stage" -czf "$temporary" tongyue-reader
mv -f -- "$temporary" "$output"
printf 'Packaged %s\n' "$output"
