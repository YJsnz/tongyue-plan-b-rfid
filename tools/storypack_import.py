"""Package or install one compiled StoryPack with bounded, verified extraction."""

import argparse
from contextlib import closing
import gzip
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import wave
from pathlib import Path

from storypack import StoryPack, identifier, normalize_question, read_json, text


MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_UNPACKED_BYTES = 96 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2048
PACK_FILES = {"book.json", "segments.json", "faq_index.sqlite", "voice.json",
              "locator/segments.mfcc", "locator/questions.mfcc", "segments.bin", "phoneme_lexicon.bin", "policy.json"}


class PackConflict(ValueError):
    pass


class LimitedStream:
    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_UNPACKED_BYTES

    def read(self, size):
        data = self.stream.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError("StoryPack exceeds expanded size limit")
        return data


def extract_book(source, stage):
    with gzip.GzipFile(fileobj=source) as compressed:
        bounded = LimitedStream(compressed)
        with tarfile.open(fileobj=bounded, mode="r|") as archive:
            return extract_members(archive, bounded, stage)


def extract_members(archive, bounded, stage):
    book_id = None
    files, folded_names = set(), set()
    for index, entry in enumerate(archive):
        if index >= MAX_ARCHIVE_ENTRIES or not 0 <= entry.size <= MAX_UNPACKED_BYTES:
            raise ValueError("StoryPack exceeds archive limits")
        if entry.name == "DEMO-INFO.json" and entry.isfile() and entry.size <= 8192:
            continue
        if entry.name == "packs" and entry.isdir():
            continue
        parts = entry.name.split("/")
        if len(parts) < 2 or parts[0] != "packs":
            raise ValueError("Expected a single packs/<bookId> directory")
        candidate = identifier(parts[1])
        if book_id is not None and candidate != book_id:
            raise ValueError("Archive contains more than one book")
        book_id = candidate
        relative = "/".join(parts[2:])
        if entry.isdir() and relative in ("", "audio_cache", "locator"):
            continue
        if entry.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or entry.issparse() or (relative not in PACK_FILES and
                not re.fullmatch(r"audio_cache/[a-zA-Z0-9_-]{1,100}\.wav", relative)):
            raise ValueError(f"Unexpected StoryPack member: {entry.name}")
        if relative.casefold() in folded_names:
            raise ValueError(f"Duplicate StoryPack member: {entry.name}")
        if relative in ("book.json", "segments.json", "voice.json", "policy.json") and entry.size > 1_048_576:
            raise ValueError("StoryPack metadata exceeds size limit")
        files.add(relative)
        folded_names.add(relative.casefold())
        target = stage / book_id / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.extractfile(entry) as data, target.open("xb") as output:
            shutil.copyfileobj(data, output, 64 * 1024)
            output.flush()
            os.fsync(output.fileno())
    while bounded.read(65536):
        pass
    if not book_id or "book.json" not in files:
        raise ValueError("No StoryPack manifest found")
    manifest = read_json(stage / book_id / "book.json")
    declared = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(declared, dict) or files != {"book.json", *declared}:
        raise ValueError("Archive files do not match the manifest")
    return book_id, files


def sync_directory(path):
    if hasattr(os, "O_DIRECTORY"):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def verify_book(pack, files):
    manifest = pack.book
    expected = {"book.json", *manifest["files"]}
    if files != expected:
        raise ValueError("Archive files do not match the manifest")
    if pack.has_text_index:
        pack.text_index()
    for key in ("title", "rights", "language"):
        text(manifest[key])
    segments = {}
    for segment in pack.segments:
        segment_id = identifier(segment["segmentId"])
        if segment_id in segments or not segment["audio"].endswith(".wav"):
            raise ValueError("Invalid segment ID or audio")
        segments[segment_id] = text(segment["text"])
    for name in expected:
        if name.endswith(".wav"):
            with wave.open(str(pack.directory / name), "rb") as audio:
                if ((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000)
                        or not audio.getnframes() or audio.getnframes() * 2 > (pack.directory / name).stat().st_size):
                    raise ValueError(f"Invalid PCM16 audio: {name}")
                remaining = audio.getnframes() * 2
                while remaining:
                    chunk = audio.readframes(min(32768, (remaining + 1) // 2))
                    if not chunk:
                        raise ValueError(f"Incomplete PCM16 audio: {name}")
                    remaining -= len(chunk)
    faq = (pack.directory / "faq_index.sqlite").resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(faq, uri=True)) as database:
        database.execute("PRAGMA trusted_schema=OFF")
        kind = database.execute("SELECT type FROM sqlite_master WHERE name='faq'").fetchone()
        if kind != ("table",):
            raise ValueError("Missing FAQ table")
        approved, seen = set(), set()
        for index, (segment, question, answer, evidence, audio) in enumerate(database.execute(
                "SELECT segment_id, question, answer, evidence, audio FROM faq LIMIT 4097")):
            if (index >= 4096 or segment not in segments or text(evidence) not in segments[segment]
                    or audio not in expected or not audio.endswith(".wav")):
                raise ValueError("Invalid FAQ evidence or audio")
            if normalize_question(question) != question or (segment, question) in seen:
                raise ValueError("Invalid or duplicate FAQ alias")
            seen.add((segment, question))
            approved.add(question)
            text(answer)
    for question in pack.voice_questions.values():
        if question["question"] not in approved or not question["audio"].endswith(".wav"):
            raise ValueError("Voice question must reference an approved FAQ and WAV")
    if any(not audio.endswith(".wav") for audio in pack.voice_prompts.values()):
        raise ValueError("Voice prompts must reference WAV audio")


def install(source, packs):
    with tempfile.TemporaryDirectory(prefix=".storypack-", dir=packs) as folder:
        stage = Path(folder)
        book_id, files = extract_book(source, stage)
        pack = StoryPack(stage, book_id)
        verify_book(pack, files)
        target = packs / book_id
        already = False
        for child in pack.directory.iterdir():
            if child.is_dir():
                sync_directory(child)
        sync_directory(pack.directory)
        try:
            if target.exists() or target.is_symlink():
                raise FileExistsError(book_id)
            pack.directory.rename(target)
        except OSError:
            if target.is_symlink():
                raise PackConflict(f"A different {book_id} is already installed")
            if not target.exists():
                raise
            try:
                same = StoryPack(packs, book_id).book == pack.book
            except (OSError, ValueError, TypeError, KeyError):
                same = False
            if not same:
                raise PackConflict(f"A different {book_id} is already installed")
            already = True
        sync_directory(packs)
        return {"bookId": book_id, "title": pack.book["title"], "version": pack.book["version"],
                "alreadyInstalled": already}


def export(pack, output):
    # Use the same single-book layout as the demo archive. Include declared files only.
    with tarfile.open(output, "x:gz") as archive:
        for name in ("book.json", *pack.book["files"]):
            archive.add(pack.directory / name, arcname=f"packs/{pack.book['bookId']}/{name}", recursive=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("archive", type=Path)
    install_parser.add_argument("packs", type=Path)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("packs", type=Path)
    export_parser.add_argument("book_id")
    export_parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "export":
            export(StoryPack(args.packs, args.book_id), args.archive)
        else:
            if args.archive.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ValueError("Compressed archive exceeds 24 MiB")
            args.packs.mkdir(parents=True, exist_ok=True)
            with args.archive.open("rb") as source:
                print(install(source, args.packs))
    except (OSError, EOFError, ValueError, TypeError, KeyError, tarfile.TarError, sqlite3.Error, wave.Error) as error:
        parser.exit(1, f"StoryPack: {error}\n")


if __name__ == "__main__":
    main()
