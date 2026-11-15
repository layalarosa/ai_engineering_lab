import os
import sys
import tempfile
import traceback
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.api import Settings
from rag.secret_config import read_secret
from rag import openai_client


class SecretConfigTests(unittest.TestCase):
    def test_file_and_env_are_exclusive_and_errors_hide_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret-path"
            path.write_bytes(b"token-with-space \r\n")
            with patch.dict(os.environ, {"EXAMPLE_FILE": str(path)}, clear=True):
                self.assertEqual(read_secret("EXAMPLE"), "token-with-space ")
            with patch.dict(os.environ, {"EXAMPLE_FILE": str(path), "EXAMPLE": "both"}, clear=True):
                with self.assertRaises(ValueError):
                    read_secret("EXAMPLE")
            path.write_bytes(b"x" * 16385)
            with patch.dict(os.environ, {"EXAMPLE_FILE": str(path)}, clear=True):
                with self.assertRaises(ValueError) as error:
                    read_secret("EXAMPLE")
                self.assertNotIn(str(path), str(error.exception))
            path.unlink()
            with patch.dict(os.environ, {"EXAMPLE_FILE": str(path)}, clear=True):
                try:
                    read_secret("EXAMPLE")
                except ValueError as error:
                    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
                    self.assertNotIn(str(path), rendered)
                else:
                    self.fail("se esperaba un error de archivo ausente")

    def test_settings_loads_file_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"RAG_EMBED_MODEL": "embed", "RAG_GENERATION_MODEL": "generate"}
            for key, value in {"DATABASE_URL": "postgresql://test", "RAG_WRITE_TOKEN": "w" * 32, "RAG_READ_TOKEN": "r" * 32}.items():
                path = Path(directory) / key
                path.write_text(value, encoding="utf-8")
                env[key + "_FILE"] = str(path)
            with patch.dict(os.environ, env, clear=True):
                settings = Settings.from_env()
            self.assertEqual(settings.dsn, "postgresql://test")
            self.assertEqual(settings.write_token, "w" * 32)
            self.assertEqual(settings.read_token, "r" * 32)

    def test_openai_client_reads_key_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_text("fixture-key\n", encoding="utf-8")
            response = b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}]}'
            with patch.dict(os.environ, {"OPENAI_API_KEY_FILE": str(path)}, clear=True), \
                 patch("rag.openai_client.urlopen", return_value=BytesIO(response)) as mock:
                self.assertEqual(openai_client.generate("q", model="model", system="s"), "ok")
                self.assertEqual(mock.call_args.args[0].get_header("Authorization"), "Bearer fixture-key")
