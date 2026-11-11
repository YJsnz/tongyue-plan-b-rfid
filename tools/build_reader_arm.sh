#!/bin/sh
# Build the locator for an ARMv7 Linux hard-float userspace.
set -eu

if [ "$#" -ne 1 ]; then
    printf 'Usage: sh tools/build_reader_arm.sh OUTPUT\n' >&2
    exit 2
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output=$1
compiler=${CXX:-arm-linux-gnueabihf-g++}
case $("$compiler" -dumpmachine) in
    arm*-linux-gnueabihf) ;;
    *) printf 'Expected an ARM Linux hard-float compiler, got %s\n' "$compiler" >&2; exit 2 ;;
esac

mkdir -p -- "$(dirname -- "$output")"
temporary=$(mktemp "${output}.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
"$compiler" -std=c++17 -O2 -DNDEBUG -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard -static \
    "$root/reader/audio_locator.cpp" "$root/reader/locate_wav.cpp" -o "$temporary"
chmod 755 "$temporary"
mv -f -- "$temporary" "$output"
printf 'Built %s\n' "$output"
