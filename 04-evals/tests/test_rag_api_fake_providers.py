import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
spec = importlib.util.spec_from_file_location("rag_api_postgres_fake_model_check", FOLDER / "rag_api_postgres_fake_model_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class FakeProviderTests(unittest.TestCase):
    def test_embedding_is_fixed_and_rejects_other_configuration(self):
        result = check.fake_embedding(["dato", "pregunta"], model=check.MODEL, base_url=check.URL)
        self.assertEqual(result, [[1.0, .25], [1.0, .25]])
        for model, url in (("real", check.URL), (check.MODEL, "http://example.invalid")):
            with self.subTest(model=model, url=url), self.assertRaises(ValueError):
                check.fake_embedding(["dato"], model=model, base_url=url)

    def test_generator_requires_exact_context_and_system(self):
        cases, fixtures, _, _ = check.sim.load_inputs(check.ROOT / "04-evals/examples/security_cases.jsonl",
                                                      check.ROOT / "04-evals/examples/security_outputs.jsonl")
        case, fixture = cases[3], fixtures[3]
        fake = check.generation_for(case, fixture)
        data = {"pregunta": case["input"], "fragmentos": [{"etiqueta":"[1]", "texto":case["attack_source"]["text"]}]}
        args = {"model":"fake-generation", "system":check.answer.SYSTEM, "base_url":check.URL}
        self.assertEqual(fake(json.dumps(data), **args), fixture["answer"])
        data["fragmentos"][0]["texto"] = "dato distinto"
        with self.assertRaises(ValueError):
            fake(json.dumps(data), **args)
        args["system"] = "otra instrucción"
        with self.assertRaises(ValueError):
            fake(json.dumps(data), **args)

    def test_server_waits_for_request_threads_on_close(self):
        self.assertFalse(check.TestServer.daemon_threads)
        self.assertTrue(check.TestServer.block_on_close)


if __name__ == "__main__":
    unittest.main()
