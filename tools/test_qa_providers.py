import io
import json
import time
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from qa_providers import (
    DeepSeekQuestionAnswerProvider,
    ProviderFailure,
    ProviderUnavailable,
    StoryPackQuestionAnswerProvider,
    create_question_answer_provider,
)
from rfid_simulator import ApiError, Reader


class FakePack:
    segments = [
        {"segmentId": "s1", "text": "小熊带着一把伞出门，因为天要下雨了。"},
        {"segmentId": "s2", "text": "小熊把伞递给小兔，因为小兔没有带伞。"},
    ]

    def answer(self, _segment_id, _question):
        return {"status": "unsupported"}

    def answer_free(self, _segment_id, _question):
        return {"status": "answered", "question": "书内关系问答", "answer": "一把伞",
                "evidence": "小熊带着一把伞出门。", "segmentId": "s1"}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.body


def completion(content, finish_reason="stop"):
    body = {"choices": [{"finish_reason": finish_reason,
                         "message": {"content": json.dumps(content, ensure_ascii=False)}}]}
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


class QuestionAnswerProviderTests(unittest.TestCase):
    def test_local_provider_preserves_default_faq_and_optional_free_questions(self):
        pack = FakePack()
        self.assertEqual(StoryPackQuestionAnswerProvider().answer(pack, "s1", "问句")["status"], "unsupported")
        self.assertEqual(StoryPackQuestionAnswerProvider(True).answer(pack, "s1", "问句")["answer"], "一把伞")
        self.assertEqual(create_question_answer_provider().name, "local")
        with self.assertRaises(ValueError):
            create_question_answer_provider("deepseek")

    def test_deepseek_sends_only_current_segment_and_returns_verified_quote(self):
        calls = []

        def transport(request, timeout):
            calls.append((request, timeout))
            return FakeResponse(completion({"status": "answered", "answer": "小熊带着一把伞出门",
                                            "evidence": "小熊带着一把伞出门"}))

        provider = DeepSeekQuestionAnswerProvider("test-secret-key", transport=transport)
        result = provider.answer(FakePack(), "s1", "小熊带了什么？")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["provider"], "deepseek")
        self.assertEqual(result["evidenceStart"], 0)
        self.assertEqual(result["evidenceEnd"], len(result["evidence"]))
        request, timeout = calls[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(timeout, 20)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret-key")
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["model"], "deepseek-flash")
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        messages = sent["messages"]
        user_text = messages[1]["content"]
        self.assertIn("小熊带了什么？", user_text)
        self.assertIn("小熊带着一把伞出门", user_text)
        self.assertNotIn("小熊把伞递给小兔", user_text)
        self.assertNotIn("test-secret-key", request.data.decode("utf-8"))

    def test_missing_key_fails_before_transport(self):
        calls = []
        provider = DeepSeekQuestionAnswerProvider("", transport=lambda *_a, **_k: calls.append(True))
        with self.assertRaises(ProviderUnavailable):
            provider.answer(FakePack(), "s1", "问题")
        self.assertEqual(calls, [])

    def test_answer_must_be_identical_to_a_unique_exact_source_quote(self):
        invalid = completion({"status": "answered", "answer": "小熊带着一把鱼",
                              "evidence": "小熊带着一把鱼"})
        provider = DeepSeekQuestionAnswerProvider("test", transport=lambda *_a, **_k: FakeResponse(invalid))
        with self.assertRaises(ProviderFailure):
            provider.answer(FakePack(), "s1", "小熊带了什么？")

        duplicate = FakePack()
        duplicate.segments = [{"segmentId": "s1", "text": "小熊带伞。小熊带伞。"}]
        quoted = completion({"status": "answered", "answer": "小熊带伞", "evidence": "小熊带伞"})
        provider = DeepSeekQuestionAnswerProvider("test", transport=lambda *_a, **_k: FakeResponse(quoted))
        with self.assertRaises(ProviderFailure):
            provider.answer(duplicate, "s1", "小熊带了什么？")

    def test_http_error_does_not_expose_api_key_or_response_body(self):
        def transport(*_args, **_kwargs):
            raise HTTPError("https://api.deepseek.com/chat/completions", 401, "unauthorized",
                            None, io.BytesIO(b"test-secret-key rejected"))

        provider = DeepSeekQuestionAnswerProvider("test-secret-key", transport=transport)
        with self.assertRaises(ProviderFailure) as failure:
            provider.answer(FakePack(), "s1", "问题")
        self.assertEqual(str(failure.exception), "DeepSeek returned HTTP 401")
        self.assertNotIn("test-secret-key", str(failure.exception))

    def test_timeout_is_reported_without_transport_details(self):
        def timeout(*_args, **_kwargs):
            raise TimeoutError("test-secret-key timed out")

        provider = DeepSeekQuestionAnswerProvider("test-secret-key", transport=timeout)
        with self.assertRaises(ProviderFailure) as failure:
            provider.answer(FakePack(), "s1", "问题")
        self.assertEqual(str(failure.exception), "DeepSeek request failed or timed out")
        self.assertNotIn("test-secret-key", str(failure.exception))

    def test_invalid_json_and_truncated_completion_are_rejected(self):
        for body in (b"not json", completion({"status": "answered", "answer": "", "evidence": ""},
                                               finish_reason="length")):
            with self.subTest(body=body):
                provider = DeepSeekQuestionAnswerProvider(
                    "test", transport=lambda *_a, _body=body, **_k: FakeResponse(_body))
                with self.assertRaises(ProviderFailure):
                    provider.answer(FakePack(), "s1", "问题")

    def test_reader_maps_unconfigured_cloud_provider_to_safe_api_error(self):
        reader = Reader.__new__(Reader)
        reader.active = {"positionUncertain": False, "segmentId": "s1", "pack": FakePack()}
        reader.qa_provider = DeepSeekQuestionAnswerProvider("", transport=lambda *_a, **_k: self.fail("network call"))
        with self.assertRaises(ApiError) as failure:
            reader.ask("小熊带了什么？")
        self.assertEqual((failure.exception.status, failure.exception.code), (503, "QA_PROVIDER_UNAVAILABLE"))

    def test_reader_keeps_only_generic_question_label_in_answer_history(self):
        answer = {"status": "answered", "answer": "小熊带着一把伞出门", "evidence": "小熊带着一把伞出门"}
        provider = DeepSeekQuestionAnswerProvider(
            "test", transport=lambda *_a, **_k: FakeResponse(completion(answer)))
        reader = Reader.__new__(Reader)
        reader.active = {"positionUncertain": False, "segmentId": "s1", "pack": FakePack(),
                         "started": time.monotonic(), "answers": []}
        reader.local_tts_dir = None
        reader.qa_provider = provider
        result = reader.ask("小熊出门时随身带的是什么？")
        reader.remember_answer(result, "text")
        stored = reader.active["answers"][0]
        self.assertEqual(stored["question"], "故事原文摘录")
        self.assertNotIn("小熊出门时随身带的是什么", json.dumps(stored, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
