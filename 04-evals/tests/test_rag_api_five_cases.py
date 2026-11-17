import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
spec = importlib.util.spec_from_file_location("rag_api_five_cases_fake_model_check", FOLDER / "rag_api_five_cases_fake_model_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class FiveCaseTests(unittest.TestCase):
    def setUp(self):
        self.cases, self.fixtures, _, _ = check.sim.load_inputs(check.ROOT / "04-evals/examples/security_cases.jsonl",
                                                                check.ROOT / "04-evals/examples/security_outputs.jsonl")

    def test_fake_generation_checks_every_source_and_question(self):
        for case, fixture in zip(self.cases, self.fixtures):
            documents = check.sim.corpus_for(case)
            fragments = [{"etiqueta":f"[{i}]", "texto":text} for i, text in enumerate(documents.values(), 1)]
            received = []
            fake = check.generation_for(case, documents, fixture, received)
            args = {"model":"fake-generation", "system":check.answer.SYSTEM, "base_url":check.one.URL}
            prompt = {"pregunta":case["input"], "fragmentos":fragments}
            with self.subTest(case=case["id"]):
                self.assertEqual(fake(json.dumps(prompt), **args), fixture["answer"])
                self.assertEqual(received, [fragments])
                prompt["fragmentos"] = fragments[:-1]
                with self.assertRaises(ValueError):
                    fake(json.dumps(prompt), **args)
                prompt["fragmentos"] = [{"etiqueta":"[1]", "texto":"documento ajeno"}]
                with self.assertRaises(ValueError):
                    fake(json.dumps(prompt), **args)

    def test_failure_stops_and_records_not_started(self):
        def exercise(case, fixture, dbname, params, record):
            self.assertEqual(dbname, check.pg.DB_NAMES[0] if case["id"] == self.cases[0]["id"] else check.pg.DB_NAMES[1])
            record["api_cleanup"] = "confirmed"
            if case["id"] == self.cases[1]["id"]:
                record["stage"] = "api_ingestion"
                raise RuntimeError("private-failure")
        with patch.object(check, "exercise_case", side_effect=exercise) as work:
            rows = check.execute_cases(self.cases, self.fixtures, {})
        self.assertEqual(work.call_count, 2)
        self.assertEqual([r["status"] for r in rows], ["completed", "failed", "not_started", "not_started", "not_started"])
        self.assertEqual((rows[1]["stage"], rows[1]["error"]), ("api_ingestion", "stage_failed"))
        self.assertNotIn("private-failure", json.dumps(rows))

    def test_unconfirmed_cleanup_stops_batch(self):
        def exercise(case, fixture, dbname, params, record):
            record["api_cleanup"] = "unconfirmed"
        with patch.object(check, "exercise_case", side_effect=exercise):
            rows = check.execute_cases(self.cases, self.fixtures, {})
        self.assertEqual([r["status"] for r in rows], ["failed"] + ["not_started"] * 4)
        self.assertEqual(rows[0]["api_cleanup"], "unconfirmed")

    def test_interrupt_stops_batch(self):
        with patch.object(check, "exercise_case", side_effect=KeyboardInterrupt):
            rows = check.execute_cases(self.cases, self.fixtures, {})
        self.assertEqual(rows[0]["error"], "interrupted")
        self.assertTrue(all(r["status"] == "not_started" for r in rows[1:]))


if __name__ == "__main__":
    unittest.main()
