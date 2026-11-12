"""Compile an authored B0 StoryPack for offline reading and evidence-based FAQ."""

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
import wave
from contextlib import closing
from pathlib import Path

from text_index import DEFAULT_POLICY, INDEX_FILES, TextIndex, compile_text, validate_policy


VOICE_PROMPTS = {"questionReady", "unsupported", "needsPositionConfirmation", "repeatQuestion"}
MAX_VOICE_QUESTIONS = 64
BANK_PATHS = {"segments": "locator/segments.mfcc", "questions": "locator/questions.mfcc"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected non-empty text")
    return value.strip()


def normalize_question(question):
    value = unicodedata.normalize("NFKC", text(question)).casefold()
    return re.sub(r"[\s。？?！!，,、]+", "", value)


def copy_audio(root, source, output):
    source = (root / text(source)).resolve()
    if not source.is_relative_to(root):
        raise ValueError("Audio must be inside the source directory")
    with wave.open(str(source), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError(f"{source.name} must be 16 kHz mono PCM16 WAV")
        if audio.getnframes() <= 0 or len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2:
            raise ValueError(f"{source.name} has no complete audio data")
    shutil.copyfile(source, output)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compile_voice(raw, root, output, approved):
    authored_questions = raw.get("voiceQuestions", [])
    authored_prompts = raw.get("voicePrompts", {})
    if not isinstance(authored_questions, list) or len(authored_questions) > MAX_VOICE_QUESTIONS:
        raise ValueError(f"voiceQuestions must contain at most {MAX_VOICE_QUESTIONS} templates")
    if not isinstance(authored_prompts, dict) or set(authored_prompts) - VOICE_PROMPTS:
        raise ValueError("voicePrompts contains an unsupported prompt name")
    if not authored_questions and not authored_prompts:
        return []
    questions, prompts, seen = [], {}, set()
    for index, authored in enumerate(authored_questions, 1):
        question = normalize_question(authored["question"])
        if question not in approved or question in seen:
            raise ValueError("Each voice question must be a distinct approved FAQ alias")
        seen.add(question)
        template_id = f"q{index}"
        audio = f"audio_cache/question-{template_id}.wav"
        copy_audio(root, authored["audio"], output / audio)
        questions.append({"id": template_id, "question": question, "audio": audio})
    for status, wav in authored_prompts.items():
        audio = f"audio_cache/prompt-{status}.wav"
        copy_audio(root, wav, output / audio)
        prompts[status] = audio
    write_json(output / "voice.json", {"questions": questions, "prompts": prompts})
    return questions


def compile_banks(output, locator, segments, questions):
    (output / "locator").mkdir()
    for kind, entries, id_field in (("segments", segments, "segmentId"), ("questions", questions, "id")):
        if not entries:
            continue
        references = [f"{entry[id_field]}={entry['audio']}" for entry in entries]
        result = subprocess.run([str(locator.resolve()), "--compile-references", BANK_PATHS[kind], *references],
                                cwd=output, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
        if result.returncode or not (output / BANK_PATHS[kind]).is_file():
            raise ValueError(f"Could not compile {kind} reference bank: {result.stderr.strip()}")


def compile_pack(source, packs, locator=None):
    source = source.resolve()
    raw = read_json(source)
    book_id = identifier(raw["bookId"])
    manifest = {"formatVersion": 1, "bookId": book_id, "version": identifier(raw["version"]),
                "title": text(raw["title"]), "language": text(raw["language"]), "rights": text(raw["rights"])}
    target = packs / book_id
    if target.exists():
        raise FileExistsError(f"Output already exists: {target}")
    packs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=packs) as temp:
        output = Path(temp) / book_id
        audio_dir = output / "audio_cache"
        audio_dir.mkdir(parents=True)
        segments = []
        segments_by_id = {}
        for raw_segment in raw["segments"]:
            segment_id = identifier(raw_segment["segmentId"])
            if segment_id in segments_by_id:
                raise ValueError(f"Duplicate segment: {segment_id}")
            audio = f"audio_cache/segment-{segment_id}.wav"
            copy_audio(source.parent, raw_segment["audio"], output / audio)
            segment = {"segmentId": segment_id, "text": text(raw_segment["text"]), "audio": audio}
            segments.append(segment)
            segments_by_id[segment_id] = segment
        if not segments:
            raise ValueError("The pack needs at least one segment")
        write_json(output / "segments.json", segments)
        manifest["textIndex"] = compile_text(raw, segments, output)
        manifest["authorization"] = {"declaration": manifest["rights"], "validation": "declaration_only"}
        approved = set()
        with closing(sqlite3.connect(output / "faq_index.sqlite")) as database, database:
            database.execute("""CREATE TABLE faq (
                segment_id TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
                evidence TEXT NOT NULL, audio TEXT NOT NULL, PRIMARY KEY (segment_id, question))""")
            for index, faq in enumerate(raw.get("faq", []), 1):
                segment_id = identifier(faq["segmentId"])
                evidence = text(faq["evidence"])
                if segment_id not in segments_by_id or evidence not in segments_by_id[segment_id]["text"]:
                    raise ValueError(f"FAQ {index} evidence must quote its own segment")
                aliases = faq["questions"]
                if not isinstance(aliases, list) or not aliases:
                    raise ValueError(f"FAQ {index} needs a list of approved question aliases")
                audio = f"audio_cache/answer-{index}.wav"
                copy_audio(source.parent, faq["audio"], output / audio)
                questions = {normalize_question(alias) for alias in aliases}
                if "" in questions:
                    raise ValueError(f"FAQ {index} has an empty question alias")
                approved.update(questions)
                database.executemany("INSERT INTO faq VALUES (?, ?, ?, ?, ?)",
                                     [(segment_id, question, text(faq["answer"]), evidence, audio)
                                      for question in sorted(questions)])
        questions = compile_voice(raw, source.parent, output, approved)
        if locator is not None:
            compile_banks(output, locator, segments, questions)
        manifest["files"] = {path.relative_to(output).as_posix(): digest(path)
                             for path in sorted(output.rglob("*")) if path.is_file()}
        write_json(output / "book.json", manifest)
        output.rename(target)
    return target


class StoryPack:
    def __init__(self, packs, book_id):
        self.directory = packs / identifier(book_id)
        self.book = read_json(self.directory / "book.json")
        if not isinstance(self.book, dict) or self.book["formatVersion"] != 1 or self.book["bookId"] != book_id:
            raise ValueError("Incompatible pack manifest")
        identifier(self.book["version"])
        files = self.book["files"]
        if not isinstance(files, dict) or not {"segments.json", "faq_index.sqlite"}.issubset(files):
            raise ValueError("Missing required files in manifest")
        for name, expected in files.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError(f"Invalid pack hash: {name}")
            path = (self.directory / name).resolve()
            if not path.is_relative_to(self.directory.resolve()) or digest(path) != expected:
                raise ValueError(f"Invalid pack file: {name}")
        self.segments = read_json(self.directory / "segments.json")
        if not isinstance(self.segments, list) or not self.segments:
            raise ValueError("Missing book segments")
        if any(not isinstance(segment, dict) or segment["audio"] not in files for segment in self.segments):
            raise ValueError("Missing segment audio")
        self.has_text_index = bool(INDEX_FILES.intersection(files) or "textIndex" in self.book)
        self._text_index = None
        if self.has_text_index:
            metadata = self.book.get("textIndex")
            if (not INDEX_FILES.issubset(files) or not isinstance(metadata, dict)
                    or metadata.get("formatVersion") != 1 or metadata.get("offsetUnit") != "unicode_codepoint"
                    or metadata.get("pinyinStyle") != "tone3_neutral5"):
                raise ValueError("Incomplete text index")
            self.policy = read_json(self.directory / "policy.json")
            validate_policy(self.policy)
        else:
            self.policy = dict(DEFAULT_POLICY)
        self.voice_questions = {}
        self.voice_prompts = {}
        if "voice.json" in files:
            voice = read_json(self.directory / "voice.json")
            if (not isinstance(voice, dict) or not isinstance(voice["questions"], list)
                    or not isinstance(voice["prompts"], dict)):
                raise ValueError("Invalid voice metadata")
            if len(voice["questions"]) > MAX_VOICE_QUESTIONS:
                raise ValueError("Too many voice questions")
            for question in voice["questions"]:
                if not isinstance(question, dict):
                    raise ValueError("Invalid voice question")
                template_id = identifier(question["id"])
                if template_id in self.voice_questions or question["audio"] not in files:
                    raise ValueError("Invalid voice question")
                text(question["question"])
                self.voice_questions[template_id] = question
            for status, audio in voice["prompts"].items():
                if status not in VOICE_PROMPTS or audio not in files:
                    raise ValueError("Invalid voice prompt")
                self.voice_prompts[status] = audio

    def text_index(self):
        if not self.has_text_index:
            raise ValueError("This legacy B0 pack has no word/syllable index; recompile it on the authoring computer")
        if self._text_index is None:
            self._text_index = TextIndex(self.directory, self.segments)
        return self._text_index

    def reference_args(self, kind, *, wav_only=False):
        bank = BANK_PATHS[kind]
        if not wav_only and bank in self.book["files"]:
            return [f"--bank={self.directory / bank}"]
        if kind == "segments":
            return [f"{entry['segmentId']}={self.directory / entry['audio']}" for entry in self.segments]
        return [f"{key}={self.directory / entry['audio']}" for key, entry in self.voice_questions.items()]

    def answer(self, segment_id, question):
        alias = normalize_question(question)
        database_uri = (self.directory / "faq_index.sqlite").resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(database_uri, uri=True)) as database:
            row = database.execute("SELECT answer, evidence, audio FROM faq WHERE segment_id=? AND question=?",
                                   (segment_id, alias)).fetchone()
        if row is None:
            return {"status": "unsupported"}
        answer, evidence, audio = row
        if audio not in self.book["files"]:
            raise ValueError("Missing FAQ audio")
        return {"status": "answered", "segmentId": segment_id, "question": alias, "answer": answer,
                "evidence": evidence, "audio": audio}

    def answer_free(self, segment_id, question):
        if not self.has_text_index:
            return {"status": "unsupported"}
        from free_question import extractive_answer
        from semantic_qa import has_semantic_intent, semantic_answer
        semantic = semantic_answer(self.segments, segment_id, question)
        if semantic.get("status") == "answered" or has_semantic_intent(question):
            return semantic
        return extractive_answer(self.text_index(), segment_id, question)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Authored JSON with WAV paths relative to the JSON file")
    parser.add_argument("packs", type=Path, help="Directory containing compiled book folders")
    parser.add_argument("--locator", type=Path, help="Host locate_wav v2 or newer for precomputing MFCC reference banks")
    args = parser.parse_args()
    try:
        print(compile_pack(args.source, args.packs, args.locator))
    except (ValueError, KeyError, TypeError, OSError, wave.Error, sqlite3.Error, subprocess.SubprocessError) as error:
        parser.exit(1, f"StoryPack: {error}\n")
