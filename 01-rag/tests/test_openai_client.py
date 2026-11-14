import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag import openai_client


class OpenAIClientTests(unittest.TestCase):
    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_embeddings_preserve_input_order(self):
        payload = {"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]}
        with patch("rag.openai_client.urlopen", return_value=BytesIO(json.dumps(payload).encode())) as mock:
            self.assertEqual(openai_client.embed(["a", "b"], model="emb"), [[1, 0], [0, 1]])
            request = mock.call_args.args[0]
            self.assertTrue(request.full_url.endswith("/v1/embeddings"))
            self.assertEqual(json.loads(request.data)["input"], ["a", "b"])

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_responses_api_and_incomplete_response(self):
        payload = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Respuesta"}]}]}
        with patch("rag.openai_client.urlopen", return_value=BytesIO(json.dumps(payload).encode())) as mock:
            self.assertEqual(openai_client.generate("pregunta", model="llm", system="reglas"), "Respuesta")
            self.assertFalse(json.loads(mock.call_args.args[0].data)["store"])
        with patch("rag.openai_client._post", return_value={"status": "incomplete", "output": []}):
            with self.assertRaises(openai_client.OpenAIError):
                openai_client.generate("pregunta", model="llm", system="reglas")

    def test_invalid_embedding_and_missing_key(self):
        with patch("rag.openai_client._post", return_value={"data": [{"index": 0, "embedding": [0, 0]}]}):
            with self.assertRaises(openai_client.OpenAIError):
                openai_client.embed(["a"], model="emb")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(openai_client.OpenAIError, "OPENAI_API_KEY"):
                openai_client.generate("q", model="llm", system="s")
