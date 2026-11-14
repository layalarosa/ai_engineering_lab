import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.answer import NO_CONTEXT, answer_question
from rag.ingestion import DocumentChunk
from rag.ollama import OllamaError, generate
from rag.search import SearchResult


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.results = [SearchResult(DocumentChunk("doc.txt", 0, "El cielo es azul."), 0.8),
                        SearchResult(DocumentChunk("otro.txt", 2, "Datos irrelevantes."), 0.1)]

    def test_question_and_filtered_context_reach_model(self):
        with patch("rag.answer.generate", return_value="Es azul [1].") as mock:
            answer = answer_question("¿De qué color?", self.results, model="llm", min_score=0.5)
        self.assertEqual(answer.text, "Es azul [1].")
        self.assertEqual(answer.sources, self.results[:1])
        data = json.loads(mock.call_args.args[0])
        self.assertEqual(data["fragmentos"], [{"etiqueta": "[1]", "texto": "El cielo es azul."}])
        self.assertEqual(data["pregunta"], "¿De qué color?")

    def test_no_context_does_not_call_model(self):
        with patch("rag.answer.generate") as mock:
            self.assertEqual(answer_question("¿Qué?", [], model="llm").text, NO_CONTEXT)
            self.assertEqual(answer_question("¿Qué?", self.results, model="llm", min_score=0.9).sources, [])
            mock.assert_not_called()

    def test_invalid_score(self):
        for score in [float("nan"), -2, 2]:
            with self.subTest(score=score), self.assertRaises(ValueError):
                answer_question("pregunta", self.results, model="llm", min_score=score)

    def test_generate_request_and_invalid_response(self):
        with patch("rag.ollama.urlopen", return_value=BytesIO(b'{"response":"respuesta", "done":true}')) as mock:
            self.assertEqual(generate("pregunta", model="llm", system="reglas"), "respuesta")
            request = mock.call_args.args[0]
            self.assertTrue(request.full_url.endswith("/api/generate"))
            self.assertEqual(json.loads(request.data), {"model": "llm", "prompt": "pregunta", "system": "reglas", "stream": False})
        for payload in [b'{}', b'{"response":"", "done":true}', b'{"response":"parcial", "done":false}', b'not json']:
            with self.subTest(payload=payload), patch("rag.ollama.urlopen", return_value=BytesIO(payload)):
                with self.assertRaises(OllamaError):
                    generate("pregunta", model="llm", system="reglas")
