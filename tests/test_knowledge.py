import json
import tempfile
import unittest
from pathlib import Path

from knowledge_models import ClassificationResult, ExtractionResult
from storage import MessageStore


class KnowledgeTests(unittest.TestCase):
    def test_schema_validation(self):
        result = ExtractionResult.model_validate(
            {
                "entries": [
                    {
                        "title": "NapCat 历史读取",
                        "content": "NapCat 支持调用群历史接口读取已有聊天消息。",
                        "category": "开发文档",
                        "keywords": ["NapCat", "OneBot"],
                        "source_message_ids": ["100"],
                        "confidence": 0.8,
                    }
                ]
            }
        )
        self.assertEqual(result.entries[0].category, "开发文档")

    def test_candidate_dedup_and_review(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "messages.sqlite3")
            row = (
                "origin",
                "标题",
                "这是一条可以进入知识库的完整内容。",
                "开发文档",
                json.dumps(["测试"], ensure_ascii=False),
                json.dumps([{"message_id": "1"}], ensure_ascii=False),
                0.8,
                "same-hash",
                1000,
            )
            self.assertEqual(store.add_knowledge_entries([row, row]), 1)
            entries = store.list_knowledge("origin", "pending")
            self.assertEqual(len(entries), 1)
            self.assertTrue(
                store.review_knowledge("origin", entries[0]["id"], "approved", "u", "", 2000)
            )
            self.assertEqual(store.get_knowledge("origin", entries[0]["id"])["status"], "approved")

    def test_classification_schema(self):
        result = ClassificationResult.model_validate(
            {
                "decisions": [
                    {
                        "message_id": "100",
                        "decision": "keep",
                        "topic_key": "napcat-history",
                        "topic_title": "NapCat 历史消息读取",
                        "reason": "包含可复用接口信息",
                        "confidence": 0.9,
                    },
                    {
                        "message_id": "101",
                        "decision": "discard",
                        "topic_key": "",
                        "topic_title": "",
                        "reason": "闲聊",
                        "confidence": 0.8,
                    },
                ]
            }
        )
        self.assertEqual(result.decisions[0].topic_key, "napcat-history")
        self.assertEqual(result.decisions[1].decision, "discard")

    def test_topic_aggregation_and_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MessageStore(Path(directory) / "messages.sqlite3")
            rows = [
                ("origin", "p", "g", "1", "u1", "甲", "接口支持历史读取", 1000),
                ("origin", "p", "g", "2", "u2", "乙", "参数是 group_id", 2000),
                ("origin", "p", "g", "3", "u3", "丙", "哈哈", 3000),
            ]
            store.add_messages(rows)
            stats = store.save_classifications(
                "origin",
                [
                    {
                        "message_id": "1",
                        "decision": "keep",
                        "topic_key": "history-api",
                        "topic_title": "历史接口",
                        "reason": "接口事实",
                        "confidence": 0.9,
                    },
                    {
                        "message_id": "2",
                        "decision": "keep",
                        "topic_key": "history-api",
                        "topic_title": "历史接口",
                        "reason": "参数事实",
                        "confidence": 0.9,
                    },
                    {
                        "message_id": "3",
                        "decision": "discard",
                        "topic_key": "",
                        "topic_title": "",
                        "reason": "闲聊",
                        "confidence": 0.9,
                    },
                ],
                2,
                4000,
            )
            self.assertEqual(stats, {"kept": 2, "discarded": 1, "topics": 1})
            topic = store.list_topics("origin")[0]
            self.assertEqual(topic["status"], "ready")
            self.assertEqual(len(store.get_topic_messages("origin", topic["id"])), 2)
            self.assertEqual(store.query_unclassified("origin", 0, 5000, 10), [])


if __name__ == "__main__":
    unittest.main()
