import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from evals_lab import grounding
from evals_lab.cases import MAX_FILE_BYTES


def line(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def lines(values, separator=b"\n"):
    return separator.join(map(line, values)) + (separator if values else b"")


def review_for(output, *, case=None, claims=(), status="complete", abstention=False):
    case = case or {"id": output["id"], "input": ""}
    return {"id": output["id"], "schema_version": 2, "case_sha256": hashlib.sha256(line(case)).hexdigest(),
            "response_sha256": hashlib.sha256(line(output)).hexdigest(),
            "review_status": status, "abstention": abstention, "claims": list(claims)}


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="evals-grounding-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.assertEqual(self.folder.parent, Path(tempfile.gettempdir()).resolve())
        self.cases = self.folder / "private-cases.jsonl"
        self.outputs = self.folder / "private-outputs.jsonl"
        self.reviews = self.folder / "private-reviews.jsonl"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
        for name, path in (("grounding_cases.jsonl", self.cases), ("grounding_outputs.jsonl", self.outputs),
                           ("grounding_reviews.jsonl", self.reviews)):
            path.write_bytes((ROOT / "examples" / name).read_bytes())

    def command(self, *extra):
        return [sys.executable, "-S", "-m", "evals_lab.grounding", "--cases", str(self.cases),
                "--outputs", str(self.outputs), "--reviews", str(self.reviews), *extra]

    def run_cli(self, *extra):
        return subprocess.run(self.command(*extra), env=self.env, cwd=self.folder, capture_output=True, timeout=20)

    def test_example_and_deterministic_redacted_report(self):
        result = self.run_cli()
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        report = json.loads(result.stdout)
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["cases"][0]["case_binding"], "matched")
        self.assertEqual(report["cases"][0]["declared_review_status"], "complete")
        self.assertEqual((report["supported"], report["insufficient"], report["total_claims_scored"]), (1, 1, 2))
        self.assertEqual(report["supported_fraction"], .5)
        self.assertEqual((report["output_coverage"], report["review_coverage"]), (1, 1))
        self.assertEqual([c["verdict"] for c in report["cases"][0]["claims"]], ["supported", "insufficient"])
        for key, path in (("cases_sha256", self.cases), ("outputs_sha256", self.outputs),
                          ("reviews_sha256", self.reviews)):
            self.assertEqual(report[key], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result.stdout, self.run_cli().stdout)
        self.assertEqual(result.stdout, (json.dumps(report, sort_keys=True, ensure_ascii=True,
                                                    separators=(",", ":")) + "\n").encode("ascii"))
        for text in (b"vectores", b"datos", b"private-"):
            self.assertNotIn(text, result.stdout + result.stderr)

    def test_classifications_and_review_coverage(self):
        output = {"id":"all", "answer":"😀ábc", "sources":[{"source_id":"s1", "document_id":"Doc_1",
                                                               "chunk_index":0, "text":"á😀sí"}]}
        def claim(name, relations):
            return {"claim_id":name, "claim":"afirmación", "answer_span":{"start":0,"end":2},
                    "evidence":[{"source_id":"s1", "start":1, "end":2, "relation":r} for r in relations]}
        claims = [claim("a", ["supports"]), claim("b", ["contradicts"]),
                  claim("c", ["supports", "contradicts"]), claim("d", ["context"])]
        empty = {"id":"empty", "answer":"", "sources":[]}
        partial = dict(output, id="partial")
        unreviewed = dict(output, id="unreviewed")
        case_ids = ["all", "empty", "partial", "unreviewed", "missing"]
        self.cases.write_bytes(lines([{"id":i, "input":""} for i in case_ids]))
        self.outputs.write_bytes(lines([partial, unreviewed, empty, output]))
        self.reviews.write_bytes(lines([review_for(output, claims=claims, abstention=True),
                                        review_for(empty, abstention=False),
                                        review_for(partial, claims=[claim("a", ["supports"])],
                                                   status="incomplete", abstention=None)]))
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["total_cases"], report["outputs_present"], report["missing_output"]), (5, 4, 1))
        self.assertEqual((report["review_complete"], report["review_incomplete"], report["review_missing"]), (2, 1, 1))
        self.assertEqual((report["output_coverage"], report["review_coverage"]), (.8, .5))
        self.assertEqual((report["supported"], report["contradicted"], report["conflicting"], report["insufficient"]), (1, 1, 1, 1))
        self.assertEqual((report["total_claims_scored"], report["claims_excluded_incomplete"]), (4, 1))
        self.assertEqual((report["complete_no_claims"], report["explicit_abstentions"], report["supported_fraction"]), (1, 1, .25))
        self.assertEqual([case["status"] for case in report["cases"]],
                         ["review_complete", "review_complete", "review_incomplete", "review_missing", "missing_output"])
        self.assertIsNone(report["cases"][1]["supported_fraction"])
        self.assertIsNone(report["cases"][2]["supported_fraction"])
        self.assertEqual(report["cases"][2]["claims"][0]["verdict"], "supported")

    def test_empty_inputs_and_crlf_line_hash(self):
        self.outputs.write_bytes(b"")
        self.reviews.write_bytes(b"")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["output_coverage"], 0)
        self.assertIsNone(report["review_coverage"])
        self.assertIsNone(report["supported_fraction"])
        output = {"id":"ejemplo", "answer":"😀", "sources":[]}
        self.outputs.write_bytes(line(output) + b"\r\n")
        case = json.loads(self.cases.read_text(encoding="utf-8"))
        self.reviews.write_bytes(lines([review_for(output, case=case)], separator=b"\r\n"))
        report = json.loads(self.run_cli().stdout)
        self.assertEqual(report["complete_no_claims"], 1)
        self.assertIsNone(report["supported_fraction"])
        self.outputs.write_bytes(line(output).replace(b'"answer":', b'"answer" :') + b"\r\n")
        invalid = self.run_cli()
        self.assertEqual((invalid.returncode, invalid.stdout), (2, b""))

    def test_case_binding_rejects_modified_question_and_representation(self):
        original = self.cases.read_bytes()
        decoded = json.loads(original)
        changes = [original.replace(b"sistema", b"servicio"),
                   original.replace(b'"input":', b'"input" :'),
                   line({"input":decoded["input"], "id":decoded["id"]}) + b"\n",
                   json.dumps(decoded, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n",
                   original.replace(b'"ejemplo"', b'"otro"')]
        for changed in changes:
            with self.subTest(changed=changed[:80]):
                self.cases.write_bytes(changed)
                result = self.run_cli()
                self.assertEqual((result.returncode, result.stdout), (2, b""))
                self.assertNotIn(b"servicio", result.stderr)
                self.assertNotIn(b"private-", result.stderr)
        self.cases.write_bytes(original)
        self.assertEqual(self.run_cli().returncode, 0)

    def test_case_line_hash_is_independent_of_line_endings_and_other_cases(self):
        original = self.cases.read_bytes()
        first = self.run_cli()
        self.cases.write_bytes(original.replace(b"\n", b"\r\n"))
        second = self.run_cli()
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        self.assertNotEqual(json.loads(first.stdout)["cases_sha256"], json.loads(second.stdout)["cases_sha256"])
        other = {"id":"otro", "input":"pregunta"}
        case = json.loads(original)
        self.cases.write_bytes(lines([case, other]))
        before = self.run_cli()
        self.assertEqual(before.returncode, 1)
        first_report = json.loads(before.stdout)
        other["input"] = "pregunta modificada"
        self.cases.write_bytes(lines([other, case]))
        after = self.run_cli()
        self.assertEqual(after.returncode, 1)
        second_report = json.loads(after.stdout)
        self.assertNotEqual(first_report["cases_sha256"], second_report["cases_sha256"])
        example = next(c for c in second_report["cases"] if c["id"] == "ejemplo")
        self.assertEqual((example["status"], example["case_binding"]), ("review_complete", "matched"))
        self.assertEqual(second_report["supported_fraction"], .5)

    def test_legacy_reviews_are_rejected_or_explicitly_unscored(self):
        review = json.loads(self.reviews.read_bytes())
        del review["schema_version"], review["case_sha256"]
        review["abstention"] = True
        self.reviews.write_bytes(lines([review]))
        rejected = self.run_cli()
        self.assertEqual((rejected.returncode, rejected.stdout), (2, b""))
        allowed = self.run_cli("--allow-unbound-reviews")
        self.assertEqual(allowed.returncode, 1, allowed.stderr)
        report = json.loads(allowed.stdout)
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual((report["review_unbound"], report["claims_excluded_unbound"]), (1, 2))
        self.assertEqual((report["review_complete"], report["total_claims_scored"], report["explicit_abstentions"]), (0, 0, 0))
        self.assertEqual((report["review_coverage"], report["supported_fraction"]), (0, None))
        case = report["cases"][0]
        self.assertEqual((case["status"], case["case_binding"], case["declared_review_status"]),
                         ("review_unbound", "absent", "complete"))
        self.assertTrue(case["abstention"])
        self.assertEqual(len(case["claims"]), 2)
        self.assertIsNone(case["supported_fraction"])
        review["response_sha256"] = "0" * 64
        self.reviews.write_bytes(lines([review]))
        self.assertEqual(self.run_cli("--allow-unbound-reviews").returncode, 2)

    def test_mixed_reviews_only_count_bound_claims(self):
        case = json.loads(self.cases.read_bytes())
        output = json.loads(self.outputs.read_bytes())
        bound = json.loads(self.reviews.read_bytes())
        other_case = {"id":"otro", "input":"Otra pregunta"}
        other_output = dict(output, id="otro")
        legacy = review_for(other_output, case=other_case, claims=bound["claims"], abstention=True)
        del legacy["schema_version"], legacy["case_sha256"]
        self.cases.write_bytes(lines([case, other_case]))
        self.outputs.write_bytes(lines([other_output, output]))
        self.reviews.write_bytes(lines([legacy, bound]))
        self.assertEqual(self.run_cli().returncode, 2)
        result = self.run_cli("--allow-unbound-reviews")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["review_complete"], report["review_unbound"], report["claims_excluded_unbound"]), (1, 1, 2))
        self.assertEqual((report["total_claims_scored"], report["supported_fraction"], report["review_coverage"]), (2, .5, .5))
        self.assertEqual(report["explicit_abstentions"], 0)

    def test_invalid_or_ambiguous_versions_never_fall_back(self):
        review = json.loads(self.reviews.read_bytes())
        mutations = []
        for value in (True, 1, 3, "2", None):
            changed = copy.deepcopy(review); changed["schema_version"] = value; mutations.append(changed)
        for value in ("0" * 64, "F" * 64, "0" * 63, None):
            changed = copy.deepcopy(review); changed["case_sha256"] = value; mutations.append(changed)
        for key in ("schema_version", "case_sha256"):
            changed = copy.deepcopy(review); del changed[key]; mutations.append(changed)
        changed = copy.deepcopy(review); changed["extra"] = "PRIVATE_SECRET"; mutations.append(changed)
        for changed in mutations:
            with self.subTest(changed=str(changed)[:100]):
                self.reviews.write_bytes(lines([changed]))
                for options in ((), ("--allow-unbound-reviews",)):
                    result = self.run_cli(*options)
                    self.assertEqual((result.returncode, result.stdout), (2, b""))
                    self.assertNotIn(b"PRIVATE_SECRET", result.stderr)

    def test_invalid_references_shapes_and_values_fail_before_report(self):
        output = json.loads(self.outputs.read_text(encoding="utf-8"))
        review = json.loads(self.reviews.read_text(encoding="utf-8"))
        for field, value in (("review_status", "wrong"), ("abstention", None),
                             ("response_sha256", "a" * 64), ("claims", [review["claims"][0]] * 2)):
            with self.subTest(field=field):
                data = copy.deepcopy(review); data[field] = value
                self.reviews.write_bytes(lines([data]))
                result = self.run_cli()
                self.assertEqual((result.returncode, result.stdout), (2, b""))
                self.assertNotIn(b"private-", result.stderr)
        mutations = [
            ("answer_end", lambda c: c["answer_span"].update(end=999)),
            ("answer_empty", lambda c: c["answer_span"].update(end=0)),
            ("bool_index", lambda c: c["answer_span"].update(start=True)),
            ("bad_source", lambda c: c["evidence"][0].update(source_id="missing")),
            ("source_end", lambda c: c["evidence"][0].update(end=999)),
            ("bad_relation", lambda c: c["evidence"][0].update(relation="PRIVATE_SECRET")),
            ("blank_claim", lambda c: c.update(claim="  ")),
            ("extra_field", lambda c: c.update(extra="x")),
            ("oversized_evidence", lambda c: c.update(evidence=c["evidence"] * 21)),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                data = copy.deepcopy(review); mutate(data["claims"][0])
                self.reviews.write_bytes(lines([data]))
                result = self.run_cli()
                self.assertEqual((result.returncode, result.stdout), (2, b""))
                self.assertNotIn(b"PRIVATE_SECRET", result.stderr)
        bad_outputs = [dict(output, sources=output["sources"] * 2),
                       dict(output, sources=[dict(output["sources"][0], chunk_index=True)]),
                       dict(output, sources=[dict(output["sources"][0], chunk_index=1000)]),
                       dict(output, answer="x" * 4001)]
        self.reviews.write_bytes(lines([review]))
        for data in bad_outputs:
            with self.subTest(output=str(data)[:100]):
                self.outputs.write_bytes(lines([data]))
                self.assertEqual(self.run_cli().returncode, 2)

    def test_jsonl_errors_unknown_ids_and_size(self):
        for data in (b'\xef\xbb\xbf{}', b'\xff', b'{"id":"ejemplo","id":"x"}\n', b'{}\n\n',
                     b'{"id":"ejemplo","answer":"\\ud800","sources":[]}\n',
                     b'{"id":"x","answer":"","sources":[],"x":NaN}\n', b'x' * (MAX_FILE_BYTES + 1)):
            with self.subTest(data=data[:80]):
                self.outputs.write_bytes(data)
                result = self.run_cli()
                self.assertEqual((result.returncode, result.stdout), (2, b""))
        self.outputs.write_bytes(lines([{"id":"unknown", "answer":"", "sources":[]}]))
        self.reviews.write_bytes(b"")
        self.assertEqual(self.run_cli().returncode, 2)
        self.outputs.write_bytes(b"")
        data = review_for({"id":"ejemplo", "answer":"", "sources":[]},
                          case=json.loads(self.cases.read_text(encoding="utf-8")))
        self.reviews.write_bytes(lines([data]))
        self.assertEqual(self.run_cli().returncode, 2)

    def test_unexpected_failure_and_closed_stdout(self):
        errors = io.StringIO()
        with patch.object(grounding, "load_inputs", side_effect=RuntimeError("PRIVATE_FAILURE")), patch.object(sys, "stderr", errors):
            self.assertEqual(grounding.main(["--cases", str(self.cases), "--outputs", str(self.outputs),
                                             "--reviews", str(self.reviews)]), 2)
        self.assertNotIn("PRIVATE_FAILURE", errors.getvalue())
        self.cases.write_bytes(lines([{"id":"c" + str(i), "input":""} for i in range(1000)]))
        self.outputs.write_bytes(b"")
        self.reviews.write_bytes(b"")
        process = subprocess.Popen(self.command(), cwd=self.folder, env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        process.stdout.close()
        process.stdout = None
        _, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 2, stderr)
        self.assertNotIn(b"Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
