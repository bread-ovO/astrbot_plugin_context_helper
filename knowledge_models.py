from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CATEGORIES = {"开发文档", "故障处理", "项目决策", "资源", "待验证"}


def _required_string(data: dict, key: str, minimum: int, maximum: int) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} 必须是字符串")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise ValueError(f"{key} 长度必须在 {minimum} 到 {maximum} 之间")
    return value


def _string_list(data: dict, key: str, minimum: int, maximum: int) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} 必须是数组")
    result = []
    for item in value:
        if not isinstance(item, (str, int)):
            raise ValueError(f"{key} 只能包含字符串")
        cleaned = str(item).strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    if not minimum <= len(result) <= maximum:
        raise ValueError(f"{key} 数量必须在 {minimum} 到 {maximum} 之间")
    return result


@dataclass(frozen=True)
class ExtractedKnowledge:
    title: str
    content: str
    category: str
    keywords: list[str]
    source_message_ids: list[str]
    confidence: float

    @classmethod
    def validate(cls, data: Any) -> "ExtractedKnowledge":
        if not isinstance(data, dict):
            raise ValueError("知识条目必须是对象")
        category = _required_string(data, "category", 1, 20)
        if category not in CATEGORIES:
            raise ValueError(f"不支持的知识分类：{category}")
        confidence = data.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence 必须是数字")
        if not 0 <= float(confidence) <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")
        return cls(
            title=_required_string(data, "title", 3, 80),
            content=_required_string(data, "content", 10, 4000),
            category=category,
            keywords=_string_list(data, "keywords", 1, 8),
            source_message_ids=_string_list(data, "source_message_ids", 1, 5),
            confidence=float(confidence),
        )


@dataclass(frozen=True)
class ExtractionResult:
    entries: list[ExtractedKnowledge]

    @classmethod
    def model_validate(cls, data: Any) -> "ExtractionResult":
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise ValueError("根对象必须包含 entries 数组")
        if len(data["entries"]) > 20:
            raise ValueError("单次最多包含 20 条知识")
        return cls(entries=[ExtractedKnowledge.validate(item) for item in data["entries"]])


@dataclass(frozen=True)
class MessageClassification:
    message_id: str
    decision: str
    topic_key: str
    topic_title: str
    reason: str
    confidence: float

    @classmethod
    def validate(cls, data: Any) -> "MessageClassification":
        if not isinstance(data, dict):
            raise ValueError("分类结果必须是对象")
        message_id = _required_string(data, "message_id", 1, 100)
        decision = _required_string(data, "decision", 4, 10)
        if decision not in {"keep", "discard"}:
            raise ValueError("decision 只能是 keep 或 discard")
        confidence = data.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence 必须是数字")
        if not 0 <= float(confidence) <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")
        topic_key = str(data.get("topic_key") or "").strip().lower()
        topic_title = str(data.get("topic_title") or "").strip()
        if decision == "keep":
            if not re_full_topic_key(topic_key):
                raise ValueError("保留消息必须提供小写英文 topic_key")
            if not 2 <= len(topic_title) <= 80:
                raise ValueError("保留消息必须提供 topic_title")
        return cls(
            message_id=message_id,
            decision=decision,
            topic_key=topic_key,
            topic_title=topic_title,
            reason=str(data.get("reason") or "").strip()[:200],
            confidence=float(confidence),
        )


def re_full_topic_key(value: str) -> bool:
    if not 1 <= len(value) <= 64:
        return False
    return all(character.islower() or character.isdigit() or character in "-_" for character in value)


@dataclass(frozen=True)
class ClassificationResult:
    decisions: list[MessageClassification]

    @classmethod
    def model_validate(cls, data: Any) -> "ClassificationResult":
        if not isinstance(data, dict) or not isinstance(data.get("decisions"), list):
            raise ValueError("根对象必须包含 decisions 数组")
        if len(data["decisions"]) > 200:
            raise ValueError("单批最多包含 200 条分类结果")
        return cls(
            decisions=[MessageClassification.validate(item) for item in data["decisions"]]
        )
