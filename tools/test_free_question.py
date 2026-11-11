"""PC prototype checks for source-grounded, conservative free-question answers."""

import unittest
from types import SimpleNamespace

from free_question import extractive_answer


class FreeQuestionTests(unittest.TestCase):
    def index(self, *sentences):
        return SimpleNamespace(segments=[{"segmentId": "s1", "text": "".join(sentences)}])

    def test_new_wording_returns_only_an_exact_sentence_from_the_current_page(self):
        source = "小熊带着一把伞出门，因为天要下雨了。小兔在树林里等它。"
        result = extractive_answer(self.index(source), "s1", "小熊出门的时候带了什么东西？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["question"], "原文检索提问")
        self.assertEqual(result["evidence"], "小熊带着一把伞出门，因为天要下雨了。")
        self.assertEqual(result["answer"], "绘本这一页写着：“小熊带着一把伞出门，因为天要下雨了。”")
        self.assertEqual(result["answerMode"], "extractive")
        self.assertEqual(result["evidenceScope"], "current")

    def test_adjacent_segment_can_answer_with_a_verbatim_and_identified_excerpt(self):
        index = SimpleNamespace(segments=[
            {"segmentId": "s0", "text": "雨停了，太阳出来了。"},
            {"segmentId": "s1", "text": "小熊走进森林。"},
            {"segmentId": "s2", "text": "小熊把伞交给小兔，因为雨下大了。"},
            {"segmentId": "s3", "text": "他们一起回家。"},
        ])
        result = extractive_answer(index, "s1", "小熊把伞交给了谁？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["segmentId"], "s2")
        self.assertEqual(result["evidence"], "小熊把伞交给小兔，因为雨下大了。")
        self.assertEqual(result["answer"], "绘本相邻段落写着：“小熊把伞交给小兔，因为雨下大了。”")
        self.assertEqual(result["evidenceScope"], "adjacent")

    def test_nearby_search_does_not_reach_two_segments_away(self):
        index = SimpleNamespace(segments=[
            {"segmentId": "s0", "text": "小熊把伞交给小兔，因为雨下大了。"},
            {"segmentId": "s1", "text": "雨停了。"},
            {"segmentId": "s2", "text": "小熊走进森林。"},
        ])
        self.assertEqual(extractive_answer(index, "s2", "小熊把伞交给了谁？")["status"], "unsupported")

    def test_equally_relevant_current_and_adjacent_evidence_is_ambiguous(self):
        excerpt = "小熊把伞交给小兔，因为雨下大了。"
        index = SimpleNamespace(segments=[
            {"segmentId": "s1", "text": excerpt},
            {"segmentId": "s2", "text": excerpt},
        ])
        self.assertEqual(extractive_answer(index, "s1", "小熊把伞交给了谁？")["status"], "unsupported")

    def test_unrelated_vague_and_out_of_page_questions_decline(self):
        index = self.index("小熊带着一把伞出门，因为天要下雨了。")
        for question in ("天气怎么样？", "小兔为什么没带伞？", "恐龙喜欢什么颜色？"):
            with self.subTest(question=question):
                self.assertEqual(extractive_answer(index, "s1", question)["status"], "unsupported")
        self.assertEqual(extractive_answer(index, "missing", "小熊出门带了什么？")["status"], "unsupported")

    def test_equal_evidence_in_two_sentences_declines_instead_of_guessing(self):
        index = self.index("小熊遇见兔子。", "小熊喜欢兔子。")
        self.assertEqual(extractive_answer(index, "s1", "小熊和兔子在这里发生了什么？")["status"], "unsupported")

    def test_non_text_or_non_chinese_query_cannot_create_an_answer(self):
        index = self.index("小熊带着一把伞出门。")
        self.assertEqual(extractive_answer(index, "s1", "what happened to the bear?")["status"], "unsupported")
        self.assertEqual(extractive_answer(index, "s1", None)["status"], "unsupported")

    def test_conversational_question_fillers_keep_the_grounded_topic(self):
        source = "小熊带着一把伞出门，因为天要下雨了。"
        index = self.index(source)
        for question in (
            "小熊出门的时候带了什么东西？",
            "小熊出门带的那个东西是什么呀？",
            "小熊出门时手里拿着啥？",
        ):
            with self.subTest(question=question):
                result = extractive_answer(index, "s1", question)
                self.assertEqual(result["status"], "answered")
                self.assertEqual(result["evidence"], source)

    def test_property_questions_need_matching_evidence_in_the_quote(self):
        umbrella = self.index("小熊带着一把伞出门，因为天要下雨了。")
        self.assertEqual(extractive_answer(umbrella, "s1", "小熊带了什么颜色的伞？")["status"], "unsupported")
        self.assertEqual(extractive_answer(umbrella, "s1", "小熊为什么带伞？")["status"], "answered")
        self.assertEqual(extractive_answer(umbrella, "s1", "小熊什么时候出门？")["status"], "unsupported")

        color = self.index("小熊戴着一顶红色的帽子。")
        result = extractive_answer(color, "s1", "小熊帽子的颜色是什么？")
        self.assertEqual(result["status"], "answered")
        self.assertIn("红色", result["evidence"])

    def test_reason_quantity_time_and_place_questions_require_matching_clues(self):
        self.assertEqual(extractive_answer(self.index("小熊带着一把伞出门。"), "s1", "小熊为什么带伞？")["status"],
                         "unsupported")
        self.assertEqual(extractive_answer(self.index("小熊摘了三朵花。"), "s1", "小熊摘了几朵花？")["status"],
                         "answered")
        self.assertEqual(extractive_answer(self.index("小熊和小兔摘下三朵花。"), "s1", "他们一共摘了多少朵花？")["status"],
                         "answered")
        self.assertEqual(extractive_answer(self.index("小熊抓到三只蝴蝶，还拿着一把伞。"), "s1", "小熊摘了几朵花？")["status"],
                         "unsupported")
        self.assertEqual(extractive_answer(self.index("周一早上，小熊出门。"), "s1", "小熊什么时候出门？")["status"],
                         "answered")
        self.assertEqual(extractive_answer(self.index("小熊在树林里等小兔。"), "s1", "小熊在什么地方等小兔？")["status"],
                         "answered")
        self.assertEqual(extractive_answer(self.index("小熊现在喜欢玩耍。"), "s1", "小熊在什么地方玩？")["status"],
                         "unsupported")


if __name__ == "__main__":
    unittest.main()
