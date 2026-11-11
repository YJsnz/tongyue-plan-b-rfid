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


if __name__ == "__main__":
    unittest.main()
