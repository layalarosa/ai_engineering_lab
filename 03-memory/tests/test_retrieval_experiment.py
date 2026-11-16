import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "retrieval" / "run.py"
spec = importlib.util.spec_from_file_location("memory_retrieval_experiment", SCRIPT)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


def case(messages, query="clave", max_chars=100, max_messages=2):
    return {"id": "prueba", "split": "desarrollo", "category": "limites", "family": "prueba",
            "query": query, "max_chars": max_chars, "max_messages": max_messages,
            "rationale": "Caso sintético de prueba.",
            "messages": [{"role": "user", "content": content, "label": label}
                         for content, label in messages]}


class RetrievalExperimentTests(unittest.TestCase):
    def test_corpus_distribution_and_reviewable_judgments(self):
        cases, digest = experiment.load_cases()
        self.assertEqual(len(cases), 36)
        self.assertEqual(len(digest), 64)
        self.assertEqual(sum(c["split"] == "desarrollo" for c in cases), 24)
        self.assertEqual(sum(c["split"] == "reservado" for c in cases), 12)
        self.assertTrue(any(sum(m["label"] == "relevante" for m in c["messages"]) > 1 for c in cases))
        self.assertTrue(any(len(c["messages"][-1]["content"]) > c["max_chars"] for c in cases))

    def test_validation_rejects_invalid_data_and_preserves_contract(self):
        valid = case([("clave", "relevante")])
        for change in (lambda c: c.update(query=""), lambda c: c.update(query="\ud800"),
                       lambda c: c.update(max_messages=True), lambda c: c["messages"][0].update(label="dudoso"),
                       lambda c: c["messages"][0].update(role="system"),
                       lambda c: c["messages"][0].update(content=" ")):
            item = copy.deepcopy(valid)
            change(item)
            with self.subTest(item=repr(item)), self.assertRaises(ValueError):
                experiment.validate_case(item)

    def test_lexical_normalizes_ties_and_preserves_distinct_positions(self):
        item = case([("CLAVE cafe\u0301", "relevante"), ("clave café", "relevante"),
                     ("otro", "otro")], query="clave café", max_messages=1)
        before = copy.deepcopy(item)
        self.assertEqual(experiment.lexical(item), [2])
        self.assertEqual(item, before)
        item["max_messages"] = 2
        self.assertEqual(experiment.lexical(item), [1, 2])
        item["query"] = "¡😀!"
        self.assertEqual(experiment.lexical(item), [])

    def test_skips_too_large_candidates_and_handles_nul(self):
        item = case([("clave\x00x", "relevante"), ("clave" + "x" * 20, "otro"),
                     ("clave larga y otra", "otro")], query="clave larga", max_chars=7)
        self.assertEqual(experiment.lexical(item), [1])
        row = experiment.run_case(item, repetitions=2)
        self.assertEqual(row["methods"]["recencia"]["error"], "ultimo_mensaje_supera_presupuesto")
        self.assertEqual(row["methods"]["lexica"]["characters"], 7)

    def test_recency_uses_positions_even_for_identical_messages(self):
        item = case([("clave", "relevante"), ("clave", "relevante"), ("final", "otro")], max_messages=2)
        memory = experiment.validate_case(item)
        self.assertEqual(experiment.recency(item, memory), {"error": None, "positions": [2, 3]})

    def test_metrics_include_empty_outputs_and_exclude_errors_explicitly(self):
        first = case([("clave", "relevante"), ("antigua", "desactualizado"), ("otra", "otro")])
        second = case([("ausente", "otro")], query="sin coincidencia")
        second["category"] = "sin_respuesta"
        third = case([("clave", "relevante"), ("mensaje demasiado largo", "otro")], max_chars=6)
        rows = [experiment.run_case(item, repetitions=1) for item in (first, second, third)]
        rec = experiment.aggregate([first, second, third], rows, "recencia")
        lex = experiment.aggregate([first, second, third], rows, "lexica")
        self.assertEqual(rec["cobertura_media"], {"valor": 0.0, "denominador": 1})
        self.assertEqual(rec["con_desactualizados"]["numerador"], 1)
        self.assertEqual(rec["errores"], {"ultimo_mensaje_supera_presupuesto": 1})
        self.assertEqual(lex["cobertura_media"], {"valor": 1.0, "denominador": 2})
        self.assertEqual(lex["precision_media"]["salidas_vacias_excluidas"], 1)
        self.assertEqual(lex["sin_respuesta_vacios"], 1)
        paired = experiment.paired([first, second, third], rows)
        self.assertEqual(paired["diferencia_media_lexica_menos_recencia"], 1.0)
        self.assertEqual(paired["casos_comparados"], 1)
        self.assertEqual(paired["casos_excluidos_por_error"], ["prueba"])

    def test_cli_works_without_dependencies_from_another_directory(self):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-S", str(SCRIPT)], cwd=directory, env=env,
                                    capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(len(report["casos"]), 36)
        self.assertEqual(report["repeticiones_por_caso_y_metodo"], 100)
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
