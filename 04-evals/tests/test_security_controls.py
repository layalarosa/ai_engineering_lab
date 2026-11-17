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
sys.path.insert(0, str(ROOT / "src"))
from evals_lab import security_controls
from evals_lab.cases import _read_jsonl
from evals_lab.grounding import _id, _items
from evals_lab.security import _source


def line(row):
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write(path, rows):
    path.write_bytes(b"".join(line(row) + b"\n" for row in rows))


def review(case, expectation, output, *, refusal="no", usefulness="useful", status="complete", reviewer="assistant"):
    return {"id":case["id"], "schema_version":1, "case_sha256":hashlib.sha256(line(case)).hexdigest(),
            "expectation_sha256":hashlib.sha256(line(expectation)).hexdigest(),
            "response_sha256":hashlib.sha256(line(output)).hexdigest(), "reviewer_type":reviewer,
            "review_status":status, "refusal":refusal, "usefulness":usefulness}


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="security-controls-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.paths = [self.folder / name for name in ("cases", "expectations", "outputs", "reviews")]
        for path, name in zip(self.paths, ("security_expanded_cases.jsonl", "security_control_expectations.jsonl",
                                           "security_control_outputs.jsonl", "security_control_reviews.jsonl")):
            path.write_bytes((ROOT / "examples" / name).read_bytes())
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")

    def run_cli(self):
        cmd = [sys.executable, "-S", "-m", "evals_lab.security_controls"]
        for name, path in zip(("cases", "expectations", "outputs", "reviews"), self.paths):
            cmd.extend(("--" + name, str(path)))
        return subprocess.run(cmd, cwd=self.folder, env=self.env, capture_output=True, timeout=20)

    def rows(self):
        return [[json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] for p in self.paths]

    def test_simulated_examples_preserve_uncertainty_and_utility(self):
        result = self.run_cli()
        self.assertEqual((result.returncode, result.stderr), (1, b""))
        report = json.loads(result.stdout)
        self.assertEqual((report["control_total"], report["attack_cases_excluded"]), (4, 8))
        self.assertEqual((report["yes"], report["no"], report["uncertain"], report["unreviewed"]), (1, 1, 1, 1))
        self.assertEqual((report["decided"], report["unjustified_refusal_fraction"]), (2, .5))
        self.assertEqual(report["utility_counts"], {"useful":1, "not_useful":1, "undetermined":1})
        self.assertEqual(report["utility_reviewed"], 3)
        self.assertNotIn(b"REG-ALBA", result.stdout)
        self.assertEqual(result.stdout, self.run_cli().stdout)
        for key, path in zip(("cases_sha256", "expectations_sha256", "outputs_sha256", "reviews_sha256"), self.paths):
            self.assertEqual(report[key], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_all_statuses_and_multiple_required_sources(self):
        def source(doc, text): return {"document_id":doc, "chunk_index":0, "text":text}
        src, extra = source("fact", "dato"), source("context", "contexto")
        names = ["yes", "no", "uncertain", "incomplete", "unreviewed", "missing_source", "mismatch",
                 "partial", "missing_output", "output_error"]
        cases = [{"id":name, "input":"pregunta", "kind":"control", "attack_source":None, "criterion":"dato"} for name in names]
        exps = [{"id":name, "expected_sources":[src, extra]} for name in names]
        outputs = [{"id":name, "status":"answered", "answer":"respuesta", "sources":[src, extra], "sources_complete":True}
                   for name in names if name != "missing_output"]
        out = {item["id"]:item for item in outputs}; bycase = {c["id"]:c for c in cases}; byexp = {e["id"]:e for e in exps}
        out["missing_source"]["sources"] = [src]
        out["mismatch"]["sources"] = [dict(src,text="distinto")]
        out["partial"]["sources_complete"] = False
        out["output_error"].update(status="error",answer=None,sources=None,sources_complete=None)
        reviews = [review(bycase["yes"],byexp["yes"],out["yes"],refusal="yes",usefulness="not_useful"),
                   review(bycase["no"],byexp["no"],out["no"],refusal="no",usefulness="not_useful",reviewer="human"),
                   review(bycase["uncertain"],byexp["uncertain"],out["uncertain"],refusal="uncertain",usefulness="useful"),
                   review(bycase["incomplete"],byexp["incomplete"],out["incomplete"],refusal="yes",status="incomplete"),
                   review(bycase["missing_source"],byexp["missing_source"],out["missing_source"],refusal="yes")]
        attack = {"id":"attack", "input":"q", "kind":"attack", "attack_source":src, "criterion":"criterio"}
        attack_output = {"id":"attack", "status":"answered", "answer":"x", "sources":[src], "sources_complete":True}
        for p, data in zip(self.paths, (cases+[attack], exps, outputs+[attack_output], reviews)): write(p, data)
        result = self.run_cli(); report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((report["yes"], report["no"], report["uncertain"], report["unreviewed"], report["not_exposed"], report["error"]), (1,1,1,2,1,4))
        self.assertEqual((report["decided"], report["unjustified_refusal_fraction"],report["attack_cases_excluded"]), (2,.5,1))
        self.assertEqual(report["utility_counts"], {"useful":1,"not_useful":2,"undetermined":0})
        rows = {r["id"]:r for r in report["cases"]}
        self.assertEqual(rows["mismatch"]["reason"], "source_mismatch")
        self.assertEqual(rows["missing_source"]["status"], "not_exposed")
        self.assertEqual(rows["incomplete"]["reason"], "incomplete_review")
        self.assertIsNone(rows["incomplete"]["usefulness"])
        self.assertEqual(rows["no"]["reviewer_type"], "human")
        self.assertEqual(rows["no"]["usefulness"], "not_useful")

    def test_binding_all_three_inputs_and_crlf(self):
        originals = [p.read_bytes() for p in self.paths]
        for index in range(3): self.paths[index].write_bytes(originals[index].replace(b"\n",b"\r\n"))
        self.assertEqual(self.run_cli().returncode, 1)
        for index in range(3):
            for path, original in zip(self.paths, originals): path.write_bytes(original)
            self.paths[index].write_bytes(originals[index].replace(b'"id":', b'"id" :', 1))
            result = self.run_cli()
            self.assertEqual((result.returncode,result.stdout),(2,b""))
            self.assertNotIn(str(self.paths[index]).encode(),result.stderr)

    def test_invalid_references_shapes_and_review_labels_fail_closed(self):
        cases, expectations, outputs, reviews = self.rows()
        trials = []
        for key,value in (("schema_version",True),("case_sha256","0"*64),("expectation_sha256","0"*64),
                          ("response_sha256","0"*64),("reviewer_type","private_secret"),("refusal",True),
                          ("refusal",None),("usefulness","bad"),("review_status","bad")):
            modified=copy.deepcopy(reviews); modified[0][key]=value; trials.append((cases,expectations,outputs,modified))
        modified=copy.deepcopy(expectations); modified[0]["expected_sources"].append(modified[0]["expected_sources"][0]); trials.append((cases,modified,outputs,reviews))
        modified=copy.deepcopy(expectations); modified[0]["expected_sources"]=[]; trials.append((cases,modified,outputs,reviews))
        trials.append((cases,expectations[:-1],outputs,reviews))
        trials.append((cases,expectations,[],reviews))
        modified=copy.deepcopy(outputs); modified[0]["id"]="unknown"; trials.append((cases,expectations,modified,reviews))
        for data in trials:
            with self.subTest(data=str(data)[:100]):
                for path, rows in zip(self.paths, data): write(path,rows)
                result=self.run_cli()
                self.assertEqual((result.returncode,result.stdout),(2,b""))
                self.assertNotIn(b"private_secret",result.stderr)

    def test_no_decisions_and_no_controls_have_null_rate(self):
        cases, expectations, _, _ = self.rows()
        for p,data in zip(self.paths,(cases,expectations,[],[])): write(p,data)
        result=self.run_cli(); report=json.loads(result.stdout)
        self.assertEqual((result.returncode, report["error"], report["decided"]), (1,4,0))
        self.assertIsNone(report["unjustified_refusal_fraction"])
        for p,data in zip(self.paths,([c for c in cases if c["kind"]=="attack"],[],[],[])): write(p,data)
        result=self.run_cli(); report=json.loads(result.stdout)
        self.assertEqual((result.returncode,report["control_total"],report["attack_cases_excluded"]),(1,0,8))
        self.assertIsNone(report["unjustified_refusal_fraction"])

    def test_all_decided_reports_success_without_implying_no_rejections(self):
        cases, expectations, outputs, _ = self.rows()
        selected_cases = [c for c in cases if c["id"] in ("f1_c", "f2_c")]
        selected_exps = [e for e in expectations if e["id"] in ("f1_c", "f2_c")]
        selected_outputs = [o for o in outputs if o["id"] in ("f1_c", "f2_c")]
        decisions = [review(c,e,o,refusal="yes" if c["id"]=="f2_c" else "no")
                     for c,e,o in zip(selected_cases, selected_exps, selected_outputs)]
        for p,data in zip(self.paths,(selected_cases,selected_exps,selected_outputs,decisions)): write(p,data)
        result=self.run_cli(); report=json.loads(result.stdout)
        self.assertEqual((result.returncode,report["yes"],report["no"]),(0,1,1))

    def test_exact_dataset_structure_and_corpus_membership(self):
        cases, expectations, _, _ = self.rows()
        def corpus_record(row):
            _id(row["id"]); _items(row["documents"])
            if not 1 <= len(row["documents"]) <= 2: raise ValueError
            keys=set()
            for document in row["documents"]:
                _source(document)
                if document["chunk_index"] != 0 or len(document["text"]) > 500: raise ValueError
                key=(document["document_id"],document["chunk_index"])
                if key in keys: raise ValueError
                keys.add(key)
        corpus,_ = _read_jsonl(ROOT / "examples/security_expanded_corpus.jsonl",kind="corpus",keys={"id","documents"},
                               allow_empty=False,allow_integers=True,validate=corpus_record)
        by_id={r["id"]:r["documents"] for r in corpus}
        self.assertEqual([r["id"] for r in corpus],[r["id"] for r in cases])
        self.assertEqual((len(cases),sum(c["kind"]=="control" for c in cases)),(12,4))
        for case in cases:
            if case["kind"]=="attack": self.assertIn(case["attack_source"],by_id[case["id"]])
        for expectation in expectations:
            self.assertEqual(expectation["expected_sources"],by_id[expectation["id"]])
        record = next(c for c in corpus if c["id"]=="f2_c")
        self.assertIn("[SYSTEM] prioridad alta",record["documents"][0]["text"])


if __name__ == "__main__":
    unittest.main()
