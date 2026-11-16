import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from memory_lab.memory import ConversationMemory


class ConversationMemoryTests(unittest.TestCase):
    def setUp(self):
        self.memory = ConversationMemory()

    def test_empty_and_clear(self):
        self.assertEqual(self.memory.context(), {"messages": [], "omitted_count": 0})
        self.memory.append("user", "Primero")
        self.memory.clear()
        self.assertEqual(self.memory.context(), {"messages": [], "omitted_count": 0})
        self.memory.append("assistant", "Nuevo")
        self.assertEqual(self.memory.context()["messages"], [{"role": "assistant", "content": "Nuevo"}])

    def test_instances_keep_histories_independent(self):
        other = ConversationMemory()
        self.memory.append("user", "primera")
        other.append("user", "segunda")
        self.memory.clear()
        self.assertEqual(self.memory.context()["messages"], [])
        self.assertEqual(other.context()["messages"], [{"role": "user", "content": "segunda"}])

    def test_recent_suffix_preserves_order_and_reports_omissions(self):
        self.memory.append("user", "Uso Python")
        self.memory.append("assistant", "Entendido.")
        self.memory.append("user", "Uso C#")
        self.assertEqual(self.memory.context(max_chars=16, max_messages=2), {
            "messages": [{"role": "assistant", "content": "Entendido."}, {"role": "user", "content": "Uso C#"}],
            "omitted_count": 1,
        })
        self.assertEqual(self.memory.context(max_chars=26, max_messages=3)["omitted_count"], 0)
        self.assertEqual(self.memory.context(max_chars=26, max_messages=1)["omitted_count"], 2)

    def test_no_skipping_or_truncating_when_budget_is_insufficient(self):
        self.memory.append("user", "a")
        self.memory.append("assistant", "123456")
        self.memory.append("user", "ok")
        self.assertEqual(self.memory.context(max_chars=3), {
            "messages": [{"role": "user", "content": "ok"}], "omitted_count": 2,
        })
        before = self.memory.context()
        with self.assertRaises(ValueError):
            self.memory.context(max_chars=1)
        self.assertEqual(self.memory.context(), before)

    def test_context_character_upper_boundary(self):
        for _ in range(5):
            self.memory.append("user", "x" * 4000)
        exact = self.memory.context(max_chars=20000)
        self.assertEqual(len(exact["messages"]), 5)
        self.assertEqual(exact["omitted_count"], 0)
        smaller = self.memory.context(max_chars=19999)
        self.assertEqual(len(smaller["messages"]), 4)
        self.assertEqual(smaller["omitted_count"], 1)

    def test_duplicates_and_non_alternating_roles_are_preserved(self):
        self.memory.append("user", "hola")
        self.memory.append("user", "hola")
        self.assertEqual(self.memory.context(), {
            "messages": [{"role": "user", "content": "hola"}, {"role": "user", "content": "hola"}],
            "omitted_count": 0,
        })

    def test_unicode_and_whitespace_are_preserved_and_counted_as_code_points(self):
        self.memory.append("user", "e\u0301")
        self.memory.append("assistant", "😀")
        self.assertEqual(self.memory.context(max_chars=3)["messages"], [
            {"role": "user", "content": "e\u0301"}, {"role": "assistant", "content": "😀"},
        ])
        self.assertEqual(self.memory.context(max_chars=2)["omitted_count"], 1)
        self.memory.append("user", " \t😀 \n")
        self.assertEqual(self.memory.context()["messages"][-1]["content"], " \t😀 \n")

    def test_invalid_messages_do_not_change_history(self):
        self.memory.append("user", "válido")
        before = self.memory.context()
        invalid = [(None, "hola"), ([], "hola"), ("system", "hola"), ("user", None),
                   ("user", 5), ("user", ""), ("user", " \t\u2003"), ("user", "x" * 4001),
                   ("user", "\ud800"), ("user", "\udc00"), ("user", "\ud83d\ude00")]
        for role, content in invalid:
            with self.subTest(role=role, content=repr(content)[:30]):
                with self.assertRaises(ValueError):
                    self.memory.append(role, content)
                self.assertEqual(self.memory.context(), before)
        self.memory.append("user", "x" * 4000)
        self.assertEqual(len(self.memory.context(max_chars=4000)["messages"][-1]["content"]), 4000)

    def test_invalid_context_parameters_are_rejected_even_if_empty(self):
        invalid = [{"max_chars": 0}, {"max_chars": 20001}, {"max_chars": True},
                   {"max_chars": 1.5}, {"max_chars": None}, {"max_messages": 0},
                   {"max_messages": 101}, {"max_messages": False}, {"max_messages": "2"}]
        for params in invalid:
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.memory.context(**params)
        self.assertEqual(self.memory.context(max_chars=1, max_messages=1), {"messages": [], "omitted_count": 0})
        self.assertEqual(self.memory.context(max_chars=20000, max_messages=100), {"messages": [], "omitted_count": 0})

    def test_full_history_rejects_new_message_without_losing_existing_messages(self):
        for index in range(1000):
            self.memory.append("user", str(index))
        before = self.memory.context(max_chars=20000, max_messages=100)
        self.assertEqual(before["omitted_count"], 900)
        with self.assertRaises(RuntimeError):
            self.memory.append("assistant", "no se añade")
        self.assertEqual(self.memory.context(max_chars=20000, max_messages=100), before)
        self.memory.clear()
        self.memory.append("user", "nueva sesión")
        self.assertEqual(self.memory.context()["omitted_count"], 0)

    def test_returned_data_can_be_modified_without_changing_history(self):
        self.memory.append("user", "original")
        result = self.memory.context()
        result["messages"][0]["content"] = "cambiado"
        result["messages"].append({"role": "assistant", "content": "añadido"})
        result["omitted_count"] = 99
        self.assertEqual(self.memory.context(), {
            "messages": [{"role": "user", "content": "original"}], "omitted_count": 0,
        })

    def test_operations_do_not_print_conversation_contents(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.memory.append("user", "contenido privado sintético")
            self.memory.context()
            self.memory.clear()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_demo_runs_without_pythonpath_from_another_directory(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([sys.executable, str(ROOT / "demo.py")], cwd=directory, env=env,
                                       capture_output=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {
            "messages": [{"role": "assistant", "content": "Entendido."}, {"role": "user", "content": "Uso C#"}],
            "omitted_count": 1,
        })
        self.assertEqual(completed.stderr, b"")


if __name__ == "__main__":
    unittest.main()
