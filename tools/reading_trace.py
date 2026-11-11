"""Bounded, source-linked reading position history; never stores speech/audio."""

import math
from uuid import uuid4


MAX_TRAJECTORY = 128
STATUSES = {"matched", "repeated", "relocated", "uncertain"}


def validated_alignment(pack, event):
    if (not isinstance(event, dict) or event.get("formatVersion") != 1 or event.get("backend") != "ctc"
            or event.get("status") not in STATUSES or type(event.get("final")) is not bool):
        raise ValueError("Invalid CTC position event")
    for field in ("startSeconds", "endSeconds"):
        value = event.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 120:
            raise ValueError("Invalid CTC position time")
    if event["startSeconds"] > event["endSeconds"]:
        raise ValueError("Reversed CTC position time")
    if event["status"] == "uncertain":
        if event.get("words") != []:
            raise ValueError("Uncertain CTC event must not assert observed words")
        return None
    record = next((item for item in pack.text_index().segments if item["segmentId"] == event.get("segmentId")), None)
    words = event.get("words")
    if record is None or not isinstance(words, list) or not 1 <= len(words) <= 512:
        raise ValueError("CTC position is outside the current book")
    previous_index, previous_end = -1, event["startSeconds"]
    for word in words:
        if not isinstance(word, dict) or type(word.get("wordIndex")) is not int:
            raise ValueError("Invalid CTC word position")
        index = word["wordIndex"]
        if not previous_index < index < len(record["words"]):
            raise ValueError("CTC words must follow source order")
        start, end, _ = record["words"][index]
        if (type(word.get("textStart")) is not int or type(word.get("textEnd")) is not int or
                word["textStart"] != start or word["textEnd"] != end or word.get("text") != record["text"][start:end]):
            raise ValueError("CTC word offsets disagree with the StoryPack")
        first, last = word.get("startSeconds"), word.get("endSeconds")
        if (type(first) not in (int, float) or type(last) not in (int, float) or
                not math.isfinite(first) or not math.isfinite(last) or not previous_end <= first < last <= event["endSeconds"] + 1e-6):
            raise ValueError("Invalid CTC word time")
        previous_index, previous_end = index, last
    if event.get("wordIndex") != words[-1]["wordIndex"]:
        raise ValueError("CTC terminal word does not match its observed words")
    return words[-1]


def append_position(active, source, status, offset_ms, word=None):
    entry = {"eventId": uuid4().hex, "offsetMs": offset_ms, "segmentId": active["segmentId"],
             "source": source, "status": status}
    if active["pack"] is not None:
        for index, segment in enumerate(active["pack"].segments):
            if segment["segmentId"] == active["segmentId"]:
                entry.update(segmentNumber=index + 1, segmentText=segment["text"][:64])
                break
    if word is not None and len(word["text"].encode("utf-16-le")) <= 256:
        entry.update({field: word[field] for field in ("wordIndex", "textStart", "textEnd", "text")})
    active["trajectory"].append(entry)
    if len(active["trajectory"]) > MAX_TRAJECTORY:
        del active["trajectory"][0]
        active["trajectoryDropped"] += 1
    if status in active["traceStats"]:
        active["traceStats"][status] += 1
