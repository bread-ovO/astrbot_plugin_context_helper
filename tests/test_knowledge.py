import json
import tempfile
import unittest
from pathlib import Path

from knowledge_models import ExtractionResult
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


if __name__ == "__main__":
    unittest.main()
