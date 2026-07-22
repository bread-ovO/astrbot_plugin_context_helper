import ast
import unittest
from pathlib import Path


class PostgresContractTests(unittest.TestCase):
    def test_executemany_is_called_on_cursor(self):
        source = Path(__file__).resolve().parents[1] / "postgres_storage.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        invalid_calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (
                node.func.attr == "executemany"
                and isinstance(owner, ast.Name)
                and owner.id == "connection"
            ):
                invalid_calls.append(node.lineno)
        self.assertEqual(invalid_calls, [])


if __name__ == "__main__":
    unittest.main()
