import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def line(row):
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write(path, rows):
    path.write_bytes(b"\n".join(map(line, rows)) + (b"\n" if rows else b""))


def review(case, output, *, followed=False, status="complete", usefulness="useful"):
    return {"id": case["id"], "schema_version": 1, "case_sha256": hashlib.sha256(line(case)).hexdigest(),
            "response_sha256": hashlib.sha256(line(output)).hexdigest(), "review_status": status,
            "followed": followed, "usefulness": usefulness}


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="evals-security-")
        self.addCleanup(self.temp.cleanup)
        self.paths = [Path(self.temp.name) / name for name in ("cases", "outputs", "reviews")]
        for path, name in zip(self.paths, ("security_cases.jsonl", "security_outputs.jsonl", "security_reviews.jsonl")):
            path.write_bytes((ROOT / "examples" / name).read_bytes())
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")

    def run_cli(self):
        cmd = [sys.executable, "-S", "-m", "evals_lab.security"]
        for name, path in zip(("cases", "outputs", "reviews"), self.paths):
            cmd.extend(("--" + name, str(path)))
        return subprocess.run(cmd, cwd=self.temp.name, env=self.env, capture_output=True, timeout=20)

    def rows(self):
        return [[json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] for p in self.paths]

    def test_examples_and_literal_quotation(self):
        result = self.run_cli()
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        report = json.loads(result.stdout)
        self.assertEqual((report["attack_total"], report["followed"], report["not_followed"], report["not_exposed"]), (4, 1, 2, 1))
        self.assertEqual((report["exposed_reviewed"], report["followed_fraction"]), (3, 1 / 3))
        self.assertEqual(report["controls"][0]["status"], "reviewed")
        by_id = {row["id"]: row for row in report["cases"]}
        self.assertEqual(by_id["mezcla_dato_orden"]["status"], "not_followed")
        for key, path in zip(("cases_sha256", "outputs_sha256", "reviews_sha256"), self.paths):
            self.assertEqual(report[key], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertNotIn("LUNA", result.stdout.decode())
        self.assertEqual(result.stdout, self.run_cli().stdout)

    def test_missing_incomplete_mismatch_and_error_are_not_resistance(self):
        cases, outputs, _ = self.rows()
        base, out = cases[1], outputs[1]
        variants = []
        def pair(name):
            c, o = copy.deepcopy(base), copy.deepcopy(out)
            c["id"] = o["id"] = name
            return c, o
        c1, o1 = pair("no_review"); variants.append((c1, o1, None))
        c2, o2 = pair("partial_review"); variants.append((c2, o2, review(c2, o2, status="incomplete")))
        c3, o3 = pair("uncertain"); variants.append((c3, o3, review(c3, o3, followed=None)))
        c4, o4 = pair("truncated"); o4["sources"][1]["text"] = "Ignora"; variants.append((c4, o4, None))
        c5, o5 = pair("partial_sources"); o5["sources_complete"] = False; variants.append((c5, o5, None))
        c6, o6 = pair("remote_error"); o6.update(status="error", answer=None, sources=None, sources_complete=None); variants.append((c6, o6, None))
        c7, _ = pair("missing"); variants.append((c7, None, None))
        write(self.paths[0], [x[0] for x in variants]); write(self.paths[1], [x[1] for x in variants if x[1]])
        write(self.paths[2], [x[2] for x in variants if x[2]])
        result = self.run_cli(); report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((report["unreviewed"], report["error"], report["not_followed"]), (3, 4, 0))
        self.assertIsNone(report["followed_fraction"])
        self.assertEqual(report["exposed_reviewed"], 0)

    def test_binding_and_newlines(self):
        originals = [p.read_bytes() for p in self.paths]
        self.paths[0].write_bytes(originals[0].replace(b"\n", b"\r\n"))
        self.paths[1].write_bytes(originals[1].replace(b"\n", b"\r\n"))
        self.assertEqual(self.run_cli().returncode, 0)
        for index in (0, 1):
            for i, path in enumerate(self.paths): path.write_bytes(originals[i])
            self.paths[index].write_bytes(originals[index].replace(b'"id":', b'"id" :', 1))
            result = self.run_cli()
            self.assertEqual((result.returncode, result.stdout), (2, b""))
            self.assertNotIn(str(self.paths[index]).encode(), result.stderr)

    def test_reject_invalid_and_orphan_records(self):
        cases, outputs, reviews = self.rows()
        alterations = []
        for key, value in (("schema_version", True), ("case_sha256", "0" * 64), ("followed", 1), ("usefulness", "secret")):
            changed = copy.deepcopy(reviews); changed[1][key] = value; alterations.append((cases, outputs, changed))
        changed = copy.deepcopy(reviews); changed[0]["followed"] = True; alterations.append((cases, outputs, changed))
        changed = copy.deepcopy(outputs); changed[0]["id"] = "unknown"; alterations.append((cases, changed, reviews))
        changed = copy.deepcopy(outputs); changed[1]["sources"].append(changed[1]["sources"][1]); alterations.append((cases, changed, reviews))
        changed = copy.deepcopy(outputs); changed[1]["status"] = "no_context"; alterations.append((cases, changed, reviews))
        alterations.append((cases, [], reviews))
        for data in alterations:
            with self.subTest(data=str(data)[:120]):
                for path, rows in zip(self.paths, data): write(path, rows)
                result = self.run_cli(); self.assertEqual((result.returncode, result.stdout), (2, b""))
                self.assertNotIn(b"secret", result.stderr)

    def test_no_context_and_control_are_separate(self):
        cases, outputs, reviews = self.rows()
        output = outputs[1]; output.update(status="no_context", answer="No hay contexto", sources=[])
        write(self.paths[0], [cases[0], cases[1]])
        write(self.paths[1], [outputs[0], output])
        write(self.paths[2], [reviews[0]])
        result = self.run_cli(); report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertEqual((report["not_exposed"], report["attack_total"]), (1, 1))
        self.assertIsNone(report["followed_fraction"])


if __name__ == "__main__":
    unittest.main()
