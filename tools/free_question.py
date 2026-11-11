"""Conservative extractive question matching for the optional PC prototype."""

import re
import unicodedata


QUESTION_FILLERS = (
    "我想知道", "请你告诉我", "可以告诉我", "能不能告诉我", "请告诉我", "告诉我",
    "我想问", "请问", "为什么", "怎么回事", "怎么样", "怎么", "如何", "什么",
    "哪里", "哪儿", "哪个", "谁", "什么时候", "多少", "故事里", "书里面", "书里",
    "说说", "想知道", "它", "他", "她", "这", "那", "我们", "他们", "你们",
    "的", "吗", "呢", "啊", "呀", "吧",
)
QUESTION_LABEL = "原文检索提问"


def query_characters(question):
    if not isinstance(question, str):
        return set()
    value = unicodedata.normalize("NFKC", question).casefold()
    for filler in QUESTION_FILLERS:
        value = value.replace(filler, " ")
    return {char for char in value if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"}


def query_bigrams(question):
    if not isinstance(question, str):
        return set()
    value = unicodedata.normalize("NFKC", question).casefold()
    for filler in QUESTION_FILLERS:
        value = value.replace(filler, " ")
    sequence = [char for char in value if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"]
    return set(zip(sequence, sequence[1:]))


def evidence_sentences(text):
    return [item.strip() for item in re.split(r"(?<=[。！？!?；;])", text) if item.strip()]


def extractive_answer(text_index, segment_id, question):
    """Return a verbatim sentence only when distinct query characters overlap strongly.

    This is a keyword-grounded excerpt, not semantic understanding or generated
    reasoning. It deliberately refuses vague, ambiguous, and out-of-page queries.
    """
    terms = query_characters(question)
    anchors = query_bigrams(question)
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
