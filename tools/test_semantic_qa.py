"""PC semantic relation QA checks: paraphrases must cite exact authored evidence."""

import json
import unittest
import time
from unittest.mock import patch

from rfid_simulator import Reader
from semantic_qa import semantic_answer


class SemanticQuestionTests(unittest.TestCase):
    def index(self, *items):
        return [dict(segmentId=segment, chapterId=chapter, storyId=story, text=text)
                for segment, chapter, story, text in items]

    def test_paraphrases_retrieve_the_object_and_its_reason(self):
        source = "小熊带着一把伞出门，因为天要下雨了。"
        segments = self.index(("s1", "c1", "rain", source))
        object_answer = semantic_answer(segments, "s1", "小熊出门时随身携带的是什么？")
        self.assertEqual(object_answer["status"], "answered")
        self.assertEqual(object_answer["answer"], "带的是一把伞。")
        self.assertEqual(object_answer["evidence"], source)
        self.assertEqual(object_answer["answerMode"], "semantic-template")
        self.assertEqual(object_answer["evidenceStart"], 0)
        self.assertEqual(object_answer["evidenceEnd"], len(source))
        self.assertEqual(source[object_answer["evidenceStart"]:object_answer["evidenceEnd"]],
                         object_answer["evidence"])

        reason = semantic_answer(segments, "s1", "小熊为什么拿着伞？")
        self.assertEqual(reason["status"], "answered")
        self.assertEqual(reason["answer"], "因为天要下雨了。")
        self.assertEqual(reason["evidence"], source)

    def test_transfer_questions_distinguish_sender_recipient_and_reason(self):
        source = "小熊把伞递给小兔，因为小兔没有带伞。"
        segments = self.index(("s2", "c1", "rain", source))
        receiver = semantic_answer(segments, "s2", "小熊把雨伞交给了谁？")
        sender = semantic_answer(segments, "s2", "是谁把雨伞送给小兔的？")
        reason = semantic_answer(segments, "s2", "小熊为什么把伞给小兔？")
        self.assertEqual((receiver["status"], receiver.get("answer")), ("answered", "交给了小兔。"))
        self.assertEqual((sender["status"], sender.get("answer")), ("answered", "是小熊。"))
        self.assertEqual((reason["status"], reason.get("answer")), ("answered", "因为小兔没有带伞。"))

    def test_color_hiding_place_and_return_destination(self):
        segments = self.index(
            ("s4", "c1", "rain", "小路两旁开满了黄色的小花。"),
            ("s6", "c1", "rain", "小熊发现小鸟躲在树叶下面。"),
            ("s10", "c1", "rain", "三个朋友高高兴兴地回到了家。"),
        )
        color = semantic_answer(segments, "s4", "小花是什么颜色？")
        hiding = semantic_answer(segments, "s6", "小鸟躲到什么地方去了？")
        destination = semantic_answer(segments, "s10", "他们最后回到哪儿了？")
        self.assertEqual((color["status"], color.get("answer")), ("answered", "是黄色。"))
        self.assertEqual((hiding["status"], hiding.get("answer")), ("answered", "在树叶下面。"))
        self.assertEqual((destination["status"], destination.get("answer")), ("answered", "在家。"))

    def test_neighbor_evidence_is_limited_to_the_same_chapter_and_story(self):
        segments = self.index(
            ("s1", "c1", "rain", "小熊走进森林。"),
            ("s2", "c1", "rain", "小熊带着一把伞出门，因为天要下雨了。"),
            ("s3", "c1", "music", "乐乐带着一把笛子出门。"),
        )
        answer = semantic_answer(segments, "s1", "小熊带了什么东西？")
        self.assertEqual(answer["status"], "answered")
        self.assertEqual((answer["segmentId"], answer["evidenceScope"]), ("s2", "adjacent"))
        self.assertEqual(semantic_answer(segments, "s2", "乐乐带了什么东西？")["status"], "unsupported")

    def test_ambiguity_wrong_entity_missing_relation_and_unanswerable_color_refuse(self):
        repeated = "小熊带着一把伞出门。"
        ambiguous = self.index(("s1", "c1", "rain", repeated), ("s2", "c1", "rain", repeated))
        self.assertEqual(semantic_answer(ambiguous, "s1", "小熊带了什么？")["status"], "unsupported")

        segments = self.index(("s1", "c1", "rain", "小熊带着一把伞出门。"),
                              ("s2", "c1", "rain", "小兔没有带伞。"))
        for question in ("小兔为什么没有带伞？", "小熊的伞是什么颜色？", "恐龙喜欢什么颜色？",
                         "恐龙带了什么东西？",
                         "这本书讲了什么？"):
            with self.subTest(question=question):
                self.assertEqual(semantic_answer(segments, "s1", question)["status"], "unsupported")
        self.assertEqual(semantic_answer(segments, "missing", "小熊带了什么？")["status"], "unsupported")

    def test_pc_reader_attaches_generated_audio_but_does_not_store_its_path_or_question(self):
        answer = {"status": "answered", "segmentId": "s1", "question": "书内关系问答",
                  "answer": "带的是一把伞。", "evidence": "小熊带着一把伞出门。",
                  "answerMode": "semantic-template"}

        class Pack:
            def answer(self, _segment, _question):
                return {"status": "unsupported"}

            def answer_free(self, _segment, _question):
                return answer

        reader = object.__new__(Reader)
        reader.allow_free_questions = True
        reader.local_tts_dir = "unused-test-output"
        reader.active = {"positionUncertain": False, "segmentId": "s1", "pack": Pack(),
                         "started": time.monotonic(), "answers": []}
        with patch("local_tts.synthesize", return_value={"path": "C:/qa/answer.wav", "voice": "Huihui",
                                                           "durationMs": 1200}):
            result = reader.ask("小熊出门时拿的是什么？")
        self.assertEqual(result["synthesizedAudio"], "C:/qa/answer.wav")
        reader.remember_answer(result, "text")
        saved = reader.active["answers"][0]
        self.assertNotIn("synthesizedAudio", saved)
        self.assertNotIn("小熊出门时拿的是什么", json.dumps(saved, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
