"""Small, evidence-grounded semantic question templates for authored StoryPacks."""

import re
import unicodedata


_SENTENCES = re.compile(r"[^。！？!?；;]+[。！？!?；;]?")
_CARRY = ("携带", "随身带", "带着", "带上", "拿着", "带了", "拿了", "带", "拿")
_TRANSFER = ("递给", "交给", "送给", "赠给", "给")
_RETURN = ("回到了", "回到", "返回", "回家")
_COLORS = ("红色", "橙色", "黄色", "绿色", "青色", "蓝色", "紫色", "黑色", "白色", "粉色", "灰色", "棕色", "褐色", "彩色",
           "红", "橙", "黄", "绿", "青", "蓝", "紫", "黑", "白", "粉", "灰", "棕", "褐")
_WHY = ("为什么", "什么原因", "为何", "为啥")
_WHERE = ("哪里", "哪儿", "什么地方", "哪边", "去哪", "去哪里", "回到哪里", "回到哪儿")
_OBJECT_QUESTION = ("什么", "啥", "哪个东西", "什么物品", "什么东西", "拿的是什么", "带的是什么")
_TRAILING_ACTION = re.compile(r"(?:出门|出发|出去|来到|走进|回到|回家|去往|然后|因为|由于|所以|替|帮忙|并且|还)(?:.*)$")
_QUESTION_ACTION = re.compile(r"为什么|什么原因|为何|为啥|携带|转交|返回|躲|藏|颜色|哪里|哪儿|什么地方|哪边")
_QUESTION_FILLER = re.compile(r"^(?:请问|我想知道|请你告诉我|请告诉我|告诉我|故事里|绘本里|书里面|书里|最后|最终|那最后)+")
_GENERIC_REFERENTS = ("它们", "他们", "她们", "我们", "大家", "它", "他", "她")


def _norm(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub("|".join(map(re.escape, sorted(_CARRY, key=len, reverse=True))), "携带", value)
    value = re.sub("|".join(map(re.escape, sorted(_TRANSFER, key=len, reverse=True))), "转交", value)
    value = re.sub("|".join(map(re.escape, sorted(_RETURN, key=len, reverse=True))), "返回", value)
    return value


def _intent(question):
    value = _norm(question)
    if any(item in value for item in _WHY):
        action = "carry" if "携带" in value else "transfer" if "转交" in value else None
        return "reason", action
    if any(item in value for item in ("什么颜色", "哪种颜色", "啥颜色", "颜色")):
        return "color", None
    if "谁" in value:
        if any(item in value for item in ("给谁", "转交谁", "转交了谁", "递给谁", "交给谁", "送给谁")):
            return "recipient", "transfer"
        return "sender", "transfer"
    if any(item in value for item in _WHERE):
        action = "return" if "返回" in value else "hide" if any(item in value for item in ("躲", "藏")) else None
        return "location", action
    if "携带" in value and any(item in value for item in _OBJECT_QUESTION):
        return "object", "carry"
    return None, None


def has_semantic_intent(question):
    return isinstance(question, str) and _intent(question)[0] is not None


def _unknown_subject(question, known_entities):
    value = _norm(question)
    match = _QUESTION_ACTION.search(value)
    prefix = value[:match.start()] if match else ""
    prefix = _QUESTION_FILLER.sub("", prefix)
    if prefix.startswith(("谁", "是谁")):
        return None
    for modifier in ("出门的时候", "出门时", "的时候", "那个时候", "随身", "带着", "时", "会", "要", "的"):
        prefix = prefix.replace(modifier, "")
    prefix = prefix.strip(" ，。！？!?；;、")
    if not prefix or any(item in prefix for item in _GENERIC_REFERENTS):
        return None
    if any(entity in prefix for entity in known_entities):
        return None
    candidate = re.search(r"([\u3400-\u9fff]{2,6})$", prefix)
    return candidate.group(1) if candidate else None


def _trim(value):
    value = value.strip(" ，。！？!?；;、的了着地呀啊吧呢 ")
    return value


def _subject(prefix):
    if "把" in prefix:
        prefix = prefix.split("把", 1)[0]
    for marker in ("发现", "看见", "听见", "遇见", "看到", "和", "跟"):
        index = prefix.rfind(marker)
        if index >= 0:
            prefix = prefix[index + len(marker):]
    return _trim(prefix)


def _main_clause(sentence):
    for marker in ("因为", "由于", "所以", "因此"):
        index = sentence.find(marker)
        if index >= 0:
            return sentence[:index], sentence[index:].rstrip("。！？!?；;")
    return sentence.rstrip("。！？!?；;"), ""


def _action_match(clause, verbs):
    matches = []
    for verb in verbs:
        start = 0
        while True:
            index = clause.find(verb, start)
            if index < 0:
                break
            matches.append((index, -len(verb), verb))
            start = index + 1
    if not matches:
        return None
    index, _, verb = min(matches)
    return index, verb


def _parse_sentence(sentence):
    clause, reason = _main_clause(sentence)
    facts = []

    transfer = _action_match(clause, _TRANSFER)
    if transfer:
        start, verb = transfer
        prefix = clause[:start]
        subject = _subject(prefix)
        object_name = prefix.split("把", 1)[1] if "把" in prefix else ""
        recipient = clause[start + len(verb):]
        recipient = _TRAILING_ACTION.sub("", recipient)
        fact = {"type": "transfer", "action": "transfer", "subject": _trim(subject),
                "object": _trim(object_name), "recipient": _trim(recipient), "reason": reason}
        if fact["subject"] and fact["recipient"]:
            facts.append(fact)

    carry = _action_match(clause, _CARRY)
    if carry:
        start, verb = carry
        prefix = clause[:start]
        if "把" in prefix:
            subject = _subject(prefix)
            object_before = prefix.split("把", 1)[1]
        else:
            subject, object_before = _subject(prefix), ""
        object_after = _TRAILING_ACTION.sub("", clause[start + len(verb):])
        object_name = _trim(object_before + object_after)
        fact = {"type": "carry", "action": "carry", "subject": _trim(subject),
                "object": object_name, "reason": reason}
        if fact["subject"] and fact["object"]:
            facts.append(fact)

    if reason:
        # Causes belong only to the action in the clause before 因为/由于.
        # A negative action stated inside the cause clause must not be mistaken
        # for the reason-giving action itself.
        actions = [fact for fact in facts if fact["action"] in ("carry", "transfer")]
        for fact in actions:
            fact["reason"] = reason

    for color in _COLORS:
        match = re.search(re.escape(color) + r"的([^，。！？!?；;]+)", sentence)
        if match:
            target = _trim(_TRAILING_ACTION.sub("", match.group(1)))
            facts.append({"type": "color", "action": None, "color": color, "target": target})

    for verb in _RETURN:
        match = re.search(re.escape(verb) + r"([^，。！？!?；;]+)", sentence)
        if match:
            facts.append({"type": "location", "action": "return", "location": _trim(match.group(1))})
            break
    for verb in ("躲在", "藏在", "跑到", "来到", "走进", "坐在"):
        match = re.search(r"([^，。！？!?；;]{1,8}?)" + re.escape(verb) + r"([^，。！？!?；;]+)", sentence)
        if match:
            facts.append({"type": "location", "action": "hide" if verb in ("躲在", "藏在") else "move",
                          "subject": _subject(match.group(1)), "location": _trim(match.group(2))})
            break
    return facts


def _fact_matches_question(fact, intent, action, question, question_entities):
    relation_type = {"object": "carry", "reason": ("carry", "transfer"),
                     "sender": "transfer", "recipient": "transfer"}.get(intent, intent)
    accepted_types = relation_type if isinstance(relation_type, tuple) else (relation_type,)
    if fact["type"] not in accepted_types or (action is not None and fact.get("action") != action):
        return False
    # Resolve explicit names against the participants in each candidate fact.
    participants = {fact.get(key, "") for key in ("subject", "object", "recipient", "target")}
    if question_entities and not question_entities.issubset(participants):
        return False
    if intent == "reason" and not fact.get("reason"):
        return False
    return True


def _answer(fact):
    if fact["type"] == "object":
        return f"带的是{fact['object']}。"
    if fact["type"] == "reason":
        return fact["reason"] + ("。" if fact["reason"][-1:] not in "。！？!?" else "")
    if fact["type"] == "sender":
        return f"是{fact['subject']}。"
    if fact["type"] == "recipient":
        return f"交给了{fact['recipient']}。"
    if fact["type"] == "color":
        return f"是{fact['color']}。"
    if fact["type"] == "location":
        return f"在{fact['location']}。"
    return ""


def semantic_answer(segments, segment_id, question):
    """Answer a supported relation question using exact StoryPack sentence evidence."""
    if not isinstance(question, str) or not question.strip():
        return {"status": "unsupported"}
    intent, action = _intent(question)
    if intent is None:
        return {"status": "unsupported"}
    current = next((index for index, item in enumerate(segments) if item.get("segmentId") == segment_id), None)
    if current is None:
        return {"status": "unsupported"}
    parsed_candidates = []
    current_record = segments[current]
    entities = set()
    for record in segments:
        for match in _SENTENCES.finditer(record.get("text", "")):
            for fact in _parse_sentence(match.group(0).strip()):
                entities.update(value for key, value in fact.items()
                                if key in ("subject", "object", "recipient", "target") and value and len(value) >= 2)
    question_entities = {entity for entity in entities if entity in question}
    if _unknown_subject(question, entities):
        return {"status": "unsupported"}
    for record_index in range(max(0, current - 1), min(len(segments), current + 2)):
        record = segments[record_index]
        distance = abs(current - record_index)
        if distance and (record.get("chapterId") != current_record.get("chapterId") or
                         record.get("storyId") != current_record.get("storyId")):
            continue
        for match in _SENTENCES.finditer(record.get("text", "")):
            sentence = match.group(0).strip()
            leading = len(match.group(0)) - len(match.group(0).lstrip())
            start, end = match.start() + leading, match.start() + leading + len(sentence)
            for parsed in _parse_sentence(sentence):
                parsed_candidates.append((distance, record, sentence, start, end, parsed))
    candidates = []
    for distance, record, sentence, start, end, parsed in parsed_candidates:
        if not _fact_matches_question(parsed, intent, action, question, question_entities):
            continue
        fact = parsed
        if intent in ("reason", "object", "sender", "recipient", "location", "color"):
            fact = {**fact, "type": intent}
        candidates.append((distance, record, sentence, start, end, fact))
    if not candidates:
        return {"status": "unsupported"}
    # Two distinct source spans are a real ambiguity; don't choose by wording score.
    source_spans = {(item[1]["segmentId"], item[3], item[4]) for item in candidates}
    answers = {_answer(item[5]) for item in candidates}
    if len(source_spans) != 1 or len(answers) != 1:
        return {"status": "unsupported"}
    distance, record, evidence, start, end, fact = candidates[0]
    answer = _answer(fact)
    if not answer:
        return {"status": "unsupported"}
    return {"status": "answered", "segmentId": record["segmentId"], "question": "书内关系问答",
            "answer": answer, "evidence": evidence, "answerMode": "semantic-template",
            "evidenceScope": "current" if distance == 0 else "adjacent",
            "evidenceStart": start, "evidenceEnd": end, "sourceTextStart": start, "sourceTextEnd": end}
