"""Check compiled word/syllable offsets, policy and 100 distinct FAQ evidence traces."""

import io
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from storypack import StoryPack, compile_pack, digest, write_json
from storypack_import import export, install
from text_index import INDEX_FILES, TextIndex


class TextIndexTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix="tongyue-text-test-")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        with wave.open(str(self.root / "reference.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"".join(struct.pack("<h", round(3000 * math.sin(i * math.tau * 440 / 16000)))
                                       for i in range(16000)))

    def source(self, count=2):
        segments = [{"segmentId": "s1", "chapterId": "c1", "storyId": "rain", "text": "小熊带着伞。",
                     "words": ["小熊", "带着", "伞"], "audio": "reference.wav"},
                    {"segmentId": "s2", "chapterId": "c1", "storyId": "music", "text": "乐乐喜欢音乐。",
                     "words": ["乐乐", "喜欢", "音乐"], "audio": "reference.wav"}]
        return {"bookId": "test-book", "version": "v1", "title": "原创索引测试", "language": "zh-CN",
                "rights": "原创文字和测试声波", "segments": segments,
                "lexicon": {"乐乐": [["le4", "le4"], ["yue4", "yue4"]]},
                "faq": [{"segmentId": segments[index % 2]["segmentId"],
                         "questions": [f"第{index}个问题发生了什么？"],
                         "answer": segments[index % 2]["text"], "evidence": segments[index % 2]["text"],
                         "audio": "reference.wav"} for index in range(count)]}

    def compile(self, raw=None, name="packs"):
        write_json(self.root / "book.json", self.source() if raw is None else raw)
        compile_pack(self.root / "book.json", self.root / name)
        return StoryPack(self.root / name, "test-book")

    def test_authored_words_polyphonic_names_and_character_offsets(self):
        pack = self.compile()
        self.assertTrue(INDEX_FILES.issubset(pack.book["files"]))
        self.assertEqual(pack.book["textIndex"]["offsetUnit"], "unicode_codepoint")
        self.assertEqual(pack.book["authorization"]["validation"], "declaration_only")
        index = pack.text_index()
        tokens = index.tokens("s1")
        self.assertEqual([item["pinyin"] for item in tokens], ["xiao3", "xiong2", "dai4", "zhe5", "san3"])
        self.assertEqual([item["wordIndex"] for item in tokens], [0, 0, 1, 1, 2])
        self.assertEqual([item["textStart"] for item in tokens], list(range(5)))
        self.assertEqual(index.segments[1]["storyId"], "music")
        music = index.tokens("s2")
        self.assertEqual([item["pinyin"] for item in music][-2:], ["yin1", "yue4"])
        name = next(choices for word, choices in index.lexicon if word == "乐乐")
        self.assertEqual(name, (("le4", "le4"), ("yue4", "yue4")))

    def test_one_hundred_distinct_queries_trace_to_their_source(self):
        raw = self.source(100)
        pack = self.compile(raw)
        for authored in raw["faq"]:
            question = authored["questions"][0]
            answer = pack.answer(authored["segmentId"], question)
            self.assertEqual(answer["status"], "answered")
            self.assertEqual(answer["evidence"], authored["evidence"])
            source = next(item for item in pack.segments if item["segmentId"] == answer["segmentId"])
            self.assertIn(answer["evidence"], source["text"])
            self.assertTrue(pack.text_index().tokens(answer["segmentId"]))
            other = "s2" if authored["segmentId"] == "s1" else "s1"
            self.assertEqual(pack.answer(other, question)["status"], "unsupported")

    def test_opt_in_free_question_returns_a_verbatim_current_segment_excerpt(self):
        pack = self.compile()
        result = pack.answer_free("s1", "小熊会带什么？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["question"], "原文检索提问")
        self.assertEqual(result["evidence"], "小熊带着伞。")
        self.assertIn(result["evidence"], pack.segments[0]["text"])
        self.assertEqual(pack.answer_free("s1", "恐龙为什么会飞？")["status"], "unsupported")
        self.assertEqual(pack.answer("s1", "第0个问题发生了什么？")["status"], "answered")

    def test_free_question_searches_one_neighbor_only_within_the_same_story(self):
        raw = self.source()
        raw["segments"][1]["storyId"] = "rain"
        same_story = self.compile(raw, "same-story-packs")
        result = same_story.answer_free("s1", "乐乐喜欢什么？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["segmentId"], "s2")
        self.assertEqual(result["evidenceScope"], "adjacent")
        self.assertIn(result["evidence"], same_story.segments[1]["text"])

        separate_story = self.compile()
        self.assertEqual(separate_story.answer_free("s1", "乐乐喜欢什么？")["status"], "unsupported")

    def test_binary_roundtrip_does_not_need_the_authoring_library(self):
        pack = self.compile()
        from unittest.mock import patch
        import builtins
        original = builtins.__import__

        def without_pinyin(name, *args, **kwargs):
            if name == "pypinyin":
                raise ImportError("not installed on the board")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_pinyin):
            restored = StoryPack(pack.directory.parent, "test-book")
            self.assertEqual(restored.text_index().tokens("s2"), pack.text_index().tokens("s2"))

    def test_invalid_word_coverage_and_unsupported_policy_fail_before_publish(self):
        raw = self.source()
        raw["segments"][0]["words"] = ["小熊", "伞"]
        with self.assertRaisesRegex(ValueError, "cover"):
            self.compile(raw)
        self.assertFalse((self.root / "packs/test-book").exists())
        raw = self.source()
        raw["policy"] = {"rawAudioRetention": "keep_forever"}
        with self.assertRaisesRegex(ValueError, "policy"):
            self.compile(raw)
        self.assertFalse((self.root / "packs/test-book").exists())

    def test_rehashed_corrupt_binary_is_rejected_on_import(self):
        pack = self.compile()
        binary = pack.directory / "segments.bin"
        data = bytearray(binary.read_bytes())
        data[-4:] = b"\xff" * 4
        binary.write_bytes(data)
        pack.book["files"]["segments.bin"] = digest(binary)
        write_json(pack.directory / "book.json", pack.book)
        archive = self.root / "corrupt.tar.gz"
        export(StoryPack(pack.directory.parent, "test-book"), archive)
        target = self.root / "installed"
        target.mkdir()
        with self.assertRaisesRegex(ValueError, "lexicon"):
            install(io.BytesIO(archive.read_bytes()), target)
        self.assertEqual(list(target.iterdir()), [])

    def test_truncated_or_mismatched_index_is_rejected(self):
        pack = self.compile()
        lexicon = pack.directory / "phoneme_lexicon.bin"
        original = lexicon.read_bytes()
        lexicon.write_bytes(original[:-1])
        with self.assertRaisesRegex(ValueError, "Truncated"):
            TextIndex(pack.directory, pack.segments)
        lexicon.write_bytes(original)
        mismatched = [{**item, "text": "另一个句子。"} for item in pack.segments]
        with self.assertRaisesRegex(ValueError, "disagree"):
            TextIndex(pack.directory, mismatched)

    def test_legacy_b0_package_remains_readable_but_has_no_syllable_claim(self):
        pack = self.compile()
        for name in INDEX_FILES:
            (pack.directory / name).unlink()
            del pack.book["files"][name]
        del pack.book["textIndex"]
        write_json(pack.directory / "book.json", pack.book)
        legacy = StoryPack(pack.directory.parent, "test-book")
        self.assertFalse(legacy.has_text_index)
        self.assertEqual(legacy.answer("s1", "第0个问题发生了什么？")["status"], "answered")
        with self.assertRaisesRegex(ValueError, "legacy"):
            legacy.text_index()


if __name__ == "__main__":
    unittest.main()
