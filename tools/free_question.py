"""Conservative extractive question matching for the optional PC prototype."""

import re
import unicodedata


QUESTION_FILLERS = (
    "我想知道", "请你告诉我", "可以告诉我", "能不能告诉我", "请告诉我", "告诉我",
    "我想问", "请问", "为什么", "怎么回事", "怎么样", "怎么", "如何", "什么",
    "哪里", "哪儿", "哪个", "谁", "什么时候", "多少", "故事里", "书里面", "书里",
    "说说", "想知道", "它", "他", "她", "这", "那", "我们", "他们", "你们",
    "什么东西", "什么物品", "什么事情", "什么地方", "什么原因", "怎么会", "是什么", "什么颜色", "哪种颜色", "几朵", "几个", "几只",
    "几本", "几次", "多少个", "在哪儿", "在哪里", "去哪里", "去哪儿", "几点钟",
    "那一天", "那天", "时候", "那个", "这个", "东西", "物品", "事情", "最后", "最终",
    "颜色", "星期几", "哪一天", "几点", "几时", "多久", "哪儿", "一共", "携带", "拿着",
    "手里拿着", "递给", "交给", "送给", "赠给", "了", "的", "吗", "呢", "啊", "呀", "吧", "啥",
)
QUESTION_LABEL = "原文检索提问"
QUESTION_ALIASES = (
    ("手里拿着", "带着"), ("随身携带", "带着"), ("携带", "带着"), ("拿着", "带着"),
    ("递给", "给"), ("交给", "给"), ("送给", "给"), ("赠给", "给"),
)
COLOR_EVIDENCE = ("红", "橙", "黄", "绿", "青", "蓝", "紫", "黑", "白", "粉", "灰", "棕", "褐", "金色", "银色", "彩色")
TIME_EVIDENCE = ("今天", "昨天", "明天", "早上", "上午", "中午", "下午", "傍晚", "晚上", "夜里", "夜晚",
                 "春天", "夏天", "秋天", "冬天", "后来", "之后", "以前", "从前", "天亮", "天黑", "点钟", "星期", "周一", "周二", "周三", "周四", "周五", "周六", "周日")
LOCATION_EVIDENCE = ("来到", "走进", "进入", "回到", "跑到", "躲在", "坐在", "住在", "去到", "回家",
                     "里面", "外面", "旁边", "附近", "门口", "森林里", "河边", "山上", "树下", "屋里")
REASON_EVIDENCE = ("因为", "所以", "由于", "为了", "因此", "既然")
NUMBER_VALUE = r"(?:[零一二两三四五六七八九十百千万半]+|[0-9]+)"
NUMBER_PATTERN = re.compile(NUMBER_VALUE + r"(?:分钟|小时|个|只|本|次|张|朵|条|颗|片|块|辆|岁|天|杯|把|根|枚)?")
LOCATION_PATTERN = re.compile(r"在[^，。！？!?；;]{1,8}(?:里|上|下|旁|边|外|中|间|处|附近)")


def normalized_question(question):
    value = unicodedata.normalize("NFKC", question).casefold()
    for source, target in QUESTION_ALIASES:
        value = value.replace(source, target)
    for filler in sorted(QUESTION_FILLERS, key=len, reverse=True):
        value = value.replace(filler, " ")
    return value


def question_constraints(question):
    value = unicodedata.normalize("NFKC", question).casefold()
    constraints = []
    if any(marker in value for marker in ("什么颜色", "颜色", "哪种颜色")):
        constraints.append("color")
    if any(marker in value for marker in ("几个", "几只", "几本", "几次", "几张", "几朵", "多少", "一共")):
        constraints.append("quantity")
    if any(marker in value for marker in ("什么时候", "几点", "多久", "哪天", "哪一天", "星期几", "几时")):
        constraints.append("time")
    if any(marker in value for marker in ("为什么", "什么原因", "怎么会")):
        constraints.append("reason")
    if any(marker in value for marker in ("哪里", "哪儿", "在哪", "去哪", "什么地方")):
        constraints.append("location")
    return constraints


def supports_question_constraints(sentence, constraints):
    for constraint in constraints:
        if constraint == "color" and not any(word in sentence for word in COLOR_EVIDENCE):
            return False
        if constraint == "quantity":
            if NUMBER_PATTERN.search(sentence) is None:
                return False
        if constraint == "time" and not any(word in sentence for word in TIME_EVIDENCE):
            return False
        if constraint == "reason" and not any(word in sentence for word in REASON_EVIDENCE):
            return False
        if constraint == "location" and not (any(word in sentence for word in LOCATION_EVIDENCE) or
                                               LOCATION_PATTERN.search(sentence)):
            return False
    return True


def query_characters(question):
    if not isinstance(question, str):
        return set()
    value = normalized_question(question)
    return {char for char in value if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"}


def query_bigrams(question):
    if not isinstance(question, str):
        return set()
    value = normalized_question(question)
    sequence = [char for char in value if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"]
    return set(zip(sequence, sequence[1:]))


def evidence_sentences(text):
    return [item.strip() for item in re.split(r"(?<=[。！？!?；;])", text) if item.strip()]


def extractive_answer(text_index, segment_id, question):
    """Return a verbatim sentence only when distinct query characters overlap strongly.

    This is a keyword-grounded excerpt, not semantic understanding or generated
    reasoning. It deliberately refuses vague, ambiguous, and out-of-page queries.
    """
    if not isinstance(question, str):
        return {"status": "unsupported"}
    terms = query_characters(question)
    anchors = query_bigrams(question)
    constraints = question_constraints(question)
    if len(terms) < 3:
        return {"status": "unsupported"}
    current_index = next((index for index, item in enumerate(text_index.segments)
                          if item["segmentId"] == segment_id), None)
    if current_index is None:
        return {"status": "unsupported"}
    candidates = []
    first = max(0, current_index - 1)
    last = min(len(text_index.segments), current_index + 2)
    current_record = text_index.segments[current_index]
    for record_index in range(first, last):
        record = text_index.segments[record_index]
        distance = abs(record_index - current_index)
        if distance and (record.get("chapterId") != current_record.get("chapterId") or
                         record.get("storyId") != current_record.get("storyId")):
            continue
        for sentence in evidence_sentences(record["text"]):
            if not supports_question_constraints(sentence, constraints):
                continue
            observed = query_characters(sentence)
            matched = terms & observed
            coverage = len(matched) / len(terms)
            sentence_chars = [char for char in sentence
                              if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"]
            matched_anchors = anchors & set(zip(sentence_chars, sentence_chars[1:]))
            if len(matched) >= 3 and coverage >= 0.6 and matched_anchors:
                candidates.append((coverage, len(matched), len(matched_anchors), distance,
                                   record["segmentId"], sentence))
    if not candidates:
        return {"status": "unsupported"}
    candidates.sort(reverse=True)
    best = candidates[0]
    if len(candidates) > 1 and candidates[1][:3] == best[:3]:
        return {"status": "unsupported"}
    scope = "这一页" if best[3] == 0 else "相邻段落"
    evidence = best[5]
    return {"status": "answered", "segmentId": best[4], "question": QUESTION_LABEL,
            "answer": "绘本" + scope + "写着：“" + evidence + "”", "evidence": evidence,
            "answerMode": "extractive", "evidenceScope": "current" if best[3] == 0 else "adjacent"}


def transcribe_question(path, acoustic):
    """Greedy PC CTC transcription for a short question clip, without retaining audio."""
    import numpy as np
    from ctc_alignment import greedy_ids
    from ctc_acoustic import read_pcm_wav

    samples = read_pcm_wav(path)
    if len(samples) > 16000 * 12:
        raise ValueError("Free-question recordings are limited to 12 seconds")
    stream, chunks = acoustic.new_stream(), []
    for offset in range(0, len(samples), 1600):
        chunks.append(stream.accept(samples[offset:offset + 1600], final=offset + 1600 >= len(samples)))
    if not chunks or not any(len(item) for item in chunks):
        return ""
    scores = np.concatenate(chunks, axis=0)
    return acoustic.decode_ids(greedy_ids(scores, acoustic.blank_id))
