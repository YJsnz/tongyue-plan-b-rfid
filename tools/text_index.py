"""Portable text/word/syllable indices. Pypinyin is needed only by the offline compiler."""

import json
import re
import struct
from pathlib import Path


INDEX_FILES = {"segments.bin", "phoneme_lexicon.bin", "policy.json"}
MAX_ITEMS = 100000
MAX_INDEX_BYTES = 8 * 1024 * 1024
SYLLABLE = re.compile(r"[a-zê]+[1-5]")
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")
DEFAULT_POLICY = {"formatVersion": 1, "ageRange": [5, 8], "questionMode": "approved_templates",
                  "evidenceScope": "current_segment", "uncertainPositionAction": "confirm",
                  "unknownQuestionAction": "decline", "rawAudioRetention": "disabled"}


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != set(DEFAULT_POLICY):
        raise ValueError("Invalid StoryPack policy fields")
    age = policy["ageRange"]
    if (not isinstance(age, list) or len(age) != 2 or any(type(item) is not int for item in age)
            or not 3 <= age[0] <= age[1] <= 18):
        raise ValueError("Invalid StoryPack age range")
    if any(policy[key] != expected for key, expected in DEFAULT_POLICY.items() if key != "ageRange"):
        raise ValueError("The runtime does not implement the requested answering/recording policy")


def variants(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 8 or
            any(not isinstance(row, list) or not 1 <= len(row) <= 64 or
                any(not isinstance(item, str) or len(item) > 16 or not SYLLABLE.fullmatch(item) for item in row) for row in value)):
        raise ValueError("Pronunciations must be lists of tone-3 pinyin syllables, including neutral tone 5")
    return tuple(tuple(row) for row in value)


def word_spans(body, authored, overrides):
    if authored is None:
        authored = []
        cursor = 0
        while cursor < len(body):
            if not body[cursor].isalnum():
                cursor += 1
                continue
            matches = [word for word in overrides if body.startswith(word, cursor)]
            word = max(matches, key=len) if matches else body[cursor]
            authored.append(word)
            cursor += len(word)
    if not isinstance(authored, list) or not authored:
        raise ValueError("Each segment needs readable words")
    result, cursor = [], 0
    for word in authored:
        if not isinstance(word, str) or not word or not any(char.isalnum() for char in word):
            raise ValueError("Invalid authored word")
        start = body.find(word, cursor)
        if start < 0 or any(char.isalnum() for char in body[cursor:start]):
            raise ValueError("Authored words must cover readable text in source order")
        result.append((start, start + len(word), word))
        cursor = start + len(word)
    if any(char.isalnum() for char in body[cursor:]):
        raise ValueError("Authored words do not cover the segment")
    return result


def write_string(stream, value):
    data = value.encode("utf-8")
    if len(data) > 1048576:
        raise ValueError("Indexed string exceeds 1 MiB")
    stream.write(struct.pack("<I", len(data)) + data)


def compile_text(raw, segments, output):
    try:
        import pypinyin
    except ImportError as error:
        raise ValueError("Install tools/requirements-authoring.txt on the authoring computer") from error
    overrides = raw.get("lexicon", {})
    if not isinstance(overrides, dict):
        raise ValueError("lexicon must map words to pronunciation alternatives")
    overrides = {word: variants(value) for word, value in overrides.items()}
    if any(not word or not any(char.isalnum() for char in word) for word in overrides):
        raise ValueError("Invalid pronunciation override word")
    lexicon, ids, records = [], {}, []
    for authored, segment in zip(raw["segments"], segments):
        body = segment["text"]
        automatic = pypinyin.pinyin(body, style=pypinyin.Style.TONE3, heteronym=False,
                                   neutral_tone_with_five=True, errors=lambda value: list(value))
        if len(automatic) != len(body):
            raise ValueError("Pinyin output did not preserve character offsets")
        words = []
        for start, end, word in word_spans(body, authored.get("words"), overrides):
            choices = overrides.get(word)
            if choices is None:
                choices = variants([[automatic[index][0] for index in range(start, end)]])
            entry = (word, choices)
            if entry not in ids:
                ids[entry] = len(lexicon)
                lexicon.append(entry)
            words.append((start, end, ids[entry]))
        chapter = authored.get("chapterId", raw["bookId"])
        story = authored.get("storyId", chapter)
        if not all(isinstance(value, str) and IDENTIFIER.fullmatch(value) for value in (chapter, story)):
            raise ValueError("Invalid chapter/story ID")
        records.append({"segmentId": segment["segmentId"], "chapterId": chapter, "storyId": story,
                        "text": body, "words": words})
    if (not 1 <= len(lexicon) <= MAX_ITEMS or
            sum(len(lexicon[word[2]][1][0]) for item in records for word in item["words"]) > MAX_ITEMS):
        raise ValueError("Text index exceeds word limit")
    with (output / "phoneme_lexicon.bin").open("wb") as stream:
        stream.write(b"TYLEX001" + struct.pack("<I", len(lexicon)))
        for word, choices in lexicon:
            write_string(stream, word)
            stream.write(struct.pack("<I", len(choices)))
            for choice in choices:
                stream.write(struct.pack("<I", len(choice)))
                for syllable in choice:
                    write_string(stream, syllable)
    with (output / "segments.bin").open("wb") as stream:
        stream.write(b"TYSEG001" + struct.pack("<I", len(records)))
        for record in records:
            for key in ("segmentId", "chapterId", "storyId", "text"):
                write_string(stream, record[key])
            stream.write(struct.pack("<I", len(record["words"])))
            for word in record["words"]:
                stream.write(struct.pack("<III", *word))
    if not isinstance(raw.get("policy", {}), dict):
        raise ValueError("policy must be an object")
    policy = {**DEFAULT_POLICY, **raw.get("policy", {})}
    validate_policy(policy)
    (output / "policy.json").write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    TextIndex(output, segments)
    return {"formatVersion": 1, "offsetUnit": "unicode_codepoint", "pinyinStyle": "tone3_neutral5",
            "compiler": "pypinyin", "compilerVersion": pypinyin.__version__}


class BinaryReader:
    def __init__(self, path, magic):
        if path.stat().st_size > MAX_INDEX_BYTES:
            raise ValueError("Text index exceeds 8 MiB")
        self.data, self.offset = path.read_bytes(), 0
        if self.take(8) != magic:
            raise ValueError("Invalid text index signature")

    def take(self, size):
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError("Truncated text index")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def number(self, maximum=MAX_ITEMS):
        result, = struct.unpack("<I", self.take(4))
        if result > maximum:
            raise ValueError("Text index count exceeds limit")
        return result

    def string(self):
        return self.take(self.number(1048576)).decode("utf-8")

    def finish(self):
        if self.offset != len(self.data):
            raise ValueError("Trailing bytes in text index")


class TextIndex:
    def __init__(self, directory, expected_segments):
        lexicon = BinaryReader(Path(directory) / "phoneme_lexicon.bin", b"TYLEX001")
        self.lexicon = []
        for _ in range(lexicon.number()):
            word = lexicon.string()
            if not word or not any(char.isalnum() for char in word):
                raise ValueError("Invalid lexicon word")
            choices = [[lexicon.string() for _ in range(lexicon.number(64))]
                       for _ in range(lexicon.number(8))]
            self.lexicon.append((word, variants(choices)))
        lexicon.finish()
        source = BinaryReader(Path(directory) / "segments.bin", b"TYSEG001")
        self.segments, total = [], 0
        count = source.number(1024)
        if not count or count != len(expected_segments):
            raise ValueError("Text index and JSON segment counts disagree")
        for expected in expected_segments:
            record = {key: source.string() for key in ("segmentId", "chapterId", "storyId", "text")}
            if (record["segmentId"] != expected["segmentId"] or record["text"] != expected["text"]
                    or any(not IDENTIFIER.fullmatch(record[key]) for key in ("segmentId", "chapterId", "storyId"))):
                raise ValueError("Text index and source text disagree")
            words, cursor = [], 0
            for _ in range(source.number()):
                start, end, lex_id = struct.unpack("<III", source.take(12))
                if (not cursor <= start < end <= len(record["text"]) or lex_id >= len(self.lexicon)
                        or record["text"][start:end] != self.lexicon[lex_id][0]
                        or any(char.isalnum() for char in record["text"][cursor:start])):
                    raise ValueError("Invalid word span or lexicon reference")
                words.append((start, end, lex_id))
                cursor = end
            total += sum(len(self.lexicon[word[2]][1][0]) for word in words)
            if not words or total > MAX_ITEMS or any(char.isalnum() for char in record["text"][cursor:]):
                raise ValueError("Invalid text index word coverage")
            record["words"] = words
            self.segments.append(record)
        source.finish()

    def tokens(self, segment_id):
        record = next((item for item in self.segments if item["segmentId"] == segment_id), None)
        if record is None:
            raise ValueError("Unknown text index segment")
        result = []
        for word_index, (start, end, lex_id) in enumerate(record["words"]):
            word, choices = self.lexicon[lex_id]
            for index, syllable in enumerate(choices[0]):
                char_start = start + index if len(word) == len(choices[0]) else start
                char_end = char_start + 1 if len(word) == len(choices[0]) else end
                result.append({"segmentId": segment_id, "tokenIndex": len(result), "wordIndex": word_index,
                               "textStart": char_start, "textEnd": char_end, "pinyin": syllable})
        return result
