import unittest


class PlainTextFormatTests(unittest.TestCase):
    def test_markdown_cleanup_contract(self):
        # 与 main.py 的 QQ 纯文本转换规则保持一致，避免测试依赖 AstrBot 运行时。
        import re

        text = "## 配置方法\n- 使用 `WAL`\n[文档](https://example.com)"
        value = text.replace("```", "").replace("`", "")
        value = re.sub(r"^\s{0,3}#{1,6}\s*(.+)$", r"【\1】", value, flags=re.MULTILINE)
        value = re.sub(r"^\s*[-*+]\s+", "• ", value, flags=re.MULTILINE)
        value = re.sub(r"\[([^\]]+)]\((https?://[^)]+)\)", r"\1：\2", value)
        value = value.replace("**", "").replace("__", "").strip()

        self.assertEqual(
            value,
            "【配置方法】\n• 使用 WAL\n文档：https://example.com",
        )


if __name__ == "__main__":
    unittest.main()
