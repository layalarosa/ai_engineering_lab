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
from evals_lab import completeness
from evals_lab.cases import MAX_FILE_BYTES, MAX_LINE_BYTES


def line(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def lines(values, separator=b"\n"):
    return separator.join(map(line, values)) + (separator if values else b"")


def make_review(case, output, items=(), status="complete"):
    return {"id":case["id"], "schema_version":1, "case_sha256":hashlib.sha256(line(case)).hexdigest(),
            "response_sha256":hashlib.sha256(line(output)).hexdigest(), "review_status":status, "items":list(items)}


class CompletenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="evals-completeness-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.assertEqual(self.folder.parent, Path(tempfile.gettempdir()).resolve())
        self.cases = self.folder / "private-cases.jsonl"
        self.outputs = self.folder / "private-outputs.jsonl"
        self.reviews = self.folder / "private-reviews.jsonl"
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1")
        for name, path in (("completeness_cases.jsonl", self.cases), ("completeness_outputs.jsonl", self.outputs),
                           ("completeness_reviews.jsonl", self.reviews)):
            path.write_bytes((ROOT / "examples" / name).read_bytes())

    def command(self, *extra):
        return [sys.executable, "-S", "-m", "evals_lab.completeness", "--cases", str(self.cases),
                "--outputs", str(self.outputs), "--reviews", str(self.reviews), *extra]

    def run_cli(self, *extra):
        return subprocess.run(self.command(*extra), cwd=self.folder, env=self.env, capture_output=True, timeout=25)

    def test_example_omission_is_descriptive_not_execution_failure(self):
        result = self.run_cli()
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        report = json.loads(result.stdout)
        self.assertEqual((report["addressed"], report["omitted"], report["uncertain"]), (1, 1, 0))
        self.assertEqual((report["addressed_fraction"], report["addressed_fraction_decided"]), (.5, .5))
        self.assertFalse(report["cases"][0]["all_addressed"])
        self.assertEqual(report["schema_version"], 1)
        for key, path in (("cases_sha256", self.cases), ("outputs_sha256", self.outputs), ("reviews_sha256", self.reviews)):
            self.assertEqual(report[key], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result.stdout, self.run_cli().stdout)
        self.assertEqual(result.stdout, (json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii"))
        for private in (b"vectores", b"sistema", b"private-"):
            self.assertNotIn(private, result.stdout + result.stderr)

    def test_all_states_and_denominators(self):
        def case(name, n):
            return {"id":name, "input":"", "expected_items":[{"item_id":"i" + str(i), "description":"punto"} for i in range(n)]}
        def output(name):
            return {"id":name, "answer":"😀ábc", "sources":[]}
        def item(index, verdict):
            return {"item_id":"i" + str(index), "verdict":verdict,
                    "answer_spans":[{"start":0,"end":2}] if verdict in ("addressed", "uncertain") else []}
        sizes = [("mixed",3),("uncertain",1),("all",1),("partial",2),("unreviewed",1),
                 ("unlabeled",0),("missing_unlabeled",0),("missing",1)]
        cases = [case(*value) for value in sizes]
        outputs = [output(name) for name, _ in sizes[:6]]
        by_case = {c["id"]:c for c in cases}; by_output = {o["id"]:o for o in outputs}
        reviews = [make_review(by_case["mixed"], by_output["mixed"], [item(0,"addressed"), item(1,"omitted"), item(2,"uncertain")]),
                   make_review(by_case["uncertain"], by_output["uncertain"], [item(0,"uncertain")]),
                   make_review(by_case["all"], by_output["all"], [item(0,"addressed")]),
                   make_review(by_case["partial"], by_output["partial"], [item(0,"addressed")], status="incomplete")]
        self.cases.write_bytes(lines(cases)); self.outputs.write_bytes(lines(list(reversed(outputs))))
        self.reviews.write_bytes(lines(list(reversed(reviews))))
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report["total_cases"], report["outputs_present"], report["eligible_cases"]), (8, 6, 5))
        self.assertEqual((report["unlabeled_cases_total"], report["unlabeled"], report["missing_output"]), (2, 1, 2))
        self.assertEqual((report["review_complete"], report["review_incomplete"], report["review_missing"]), (3, 1, 1))
        self.assertEqual((report["addressed"], report["omitted"], report["uncertain"], report["items_excluded_incomplete"]), (2, 1, 2, 1))
        self.assertEqual((report["total_items_scored"], report["output_coverage"], report["review_coverage"]), (5, .75, .6))
        self.assertEqual((report["addressed_fraction"], report["addressed_fraction_decided"]), (.4, 2/3))
        details = {row["id"]:row for row in report["cases"]}
        self.assertIs(details["mixed"]["all_addressed"], False)
        self.assertIsNone(details["uncertain"]["all_addressed"])
        self.assertIs(details["all"]["all_addressed"], True)
        self.assertIsNone(details["partial"]["all_addressed"])
        self.assertEqual(details["missing_unlabeled"]["status"], "missing_output")
        self.assertFalse(details["missing_unlabeled"]["has_expected_items"])
        self.assertEqual(details["unlabeled"]["status"], "unlabeled")

    def test_explicit_negation_can_address_topic_without_claiming_correctness(self):
        case = {"id":"n", "input":"", "expected_items":[{"item_id":"storage", "description":"El sistema almacena vectores."}]}
        output = {"id":"n", "answer":"El sistema no almacena vectores.", "sources":[]}
        item = {"item_id":"storage", "verdict":"addressed", "answer_spans":[{"start":0,"end":len(output["answer"])}]}
        self.cases.write_bytes(lines([case])); self.outputs.write_bytes(lines([output]))
        self.reviews.write_bytes(lines([make_review(case, output, [item])]))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        self.assertTrue(report["cases"][0]["all_addressed"])
        self.assertEqual(report["addressed_fraction"], 1)
        self.assertNotIn("correct", report)

    def test_no_eligible_cases_and_uncertain_denominators(self):
        case = {"id":"empty", "input":"", "expected_items":[]}
        self.cases.write_bytes(lines([case])); self.outputs.write_bytes(b""); self.reviews.write_bytes(b"")
        result = self.run_cli(); report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((report["missing_output"], report["unlabeled_cases_total"]), (1, 1))
        self.assertIsNone(report["review_coverage"])
        self.assertIsNone(report["addressed_fraction"])
        self.assertIsNone(report["addressed_fraction_decided"])
        case["expected_items"] = [{"item_id":"a","description":"dato"}]
        output = {"id":"empty", "answer":"", "sources":[]}
        review = make_review(case, output, [{"item_id":"a","verdict":"uncertain","answer_spans":[]}])
        self.cases.write_bytes(lines([case])); self.outputs.write_bytes(lines([output])); self.reviews.write_bytes(lines([review]))
        result = self.run_cli(); report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual((report["uncertain"], report["addressed_fraction"]), (1, 0))
        self.assertIsNone(report["addressed_fraction_decided"])

    def test_binding_hashes_and_crlf(self):
        original_cases, original_outputs = self.cases.read_bytes(), self.outputs.read_bytes()
        before = json.loads(self.run_cli().stdout)
        self.cases.write_bytes(original_cases.replace(b"\n", b"\r\n"))
        self.outputs.write_bytes(original_outputs.replace(b"\n", b"\r\n"))
        after_run = self.run_cli(); self.assertEqual(after_run.returncode, 0, after_run.stderr)
        after = json.loads(after_run.stdout)
        self.assertNotEqual(before["cases_sha256"], after["cases_sha256"])
        self.assertNotEqual(before["outputs_sha256"], after["outputs_sha256"])
        for path, original, modified in ((self.cases, original_cases, original_cases.replace(b"vectores", b"datos", 1)),
                                         (self.cases, original_cases, original_cases.replace(b'"input":', b'"input" :')),
                                         (self.outputs, original_outputs, original_outputs.replace(b"vectores", b"datos", 1))):
            with self.subTest(file=path.name):
                self.cases.write_bytes(original_cases); self.outputs.write_bytes(original_outputs)
                path.write_bytes(modified)
                result = self.run_cli()
                self.assertEqual((result.returncode, result.stdout), (2, b""))
                self.assertNotIn(b"private-", result.stderr)

    def test_invalid_shapes_cross_references_and_versions(self):
        case = json.loads(self.cases.read_bytes()); output = json.loads(self.outputs.read_bytes())
        review = json.loads(self.reviews.read_bytes())
        mutations = []
        for key,value in (("schema_version", True), ("schema_version", 2), ("case_sha256", "0" * 64),
                          ("response_sha256", "X" * 64), ("review_status", "wrong")):
            obj = copy.deepcopy(review); obj[key] = value; mutations.append((key,obj))
        obj=copy.deepcopy(review); obj["items"]=[obj["items"][0]]; mutations.append(("missing judgment",obj))
        obj=copy.deepcopy(review); obj["items"].append(obj["items"][0]); mutations.append(("duplicate judgment",obj))
        for key,value in (("item_id","unknown"), ("verdict","PRIVATE_SECRET"), ("answer_spans", [])):
            obj=copy.deepcopy(review); obj["items"][0][key]=value; mutations.append((key,obj))
        obj=copy.deepcopy(review); obj["items"][0]["answer_spans"][0]["start"]=True; mutations.append(("boolean span",obj))
        obj=copy.deepcopy(review); obj["items"][0]["answer_spans"][0]["end"]=999; mutations.append(("span outside answer",obj))
        obj=copy.deepcopy(review); obj["items"][1]["answer_spans"]=[{"start":0,"end":1}]; mutations.append(("omitted with span",obj))
        obj=copy.deepcopy(review); obj["extra"]="PRIVATE_SECRET"; mutations.append(("extra",obj))
        for label,obj in mutations:
            with self.subTest(label=label):
                self.reviews.write_bytes(lines([obj])); result=self.run_cli()
                self.assertEqual((result.returncode,result.stdout),(2,b""))
                self.assertNotIn(b"PRIVATE_SECRET",result.stderr)
        self.reviews.write_bytes(lines([review]))
        for changed in (dict(case,expected_items=case["expected_items"]*2),
                        dict(case,expected_items=[{"item_id":"x","description":" "}]),
                        dict(case,expected_items=case["expected_items"]*11)):
            with self.subTest(case=str(changed)[:120]):
                self.cases.write_bytes(lines([changed])); self.assertEqual(self.run_cli().returncode,2)
        case["expected_items"]=[]
        self.cases.write_bytes(lines([case])); self.reviews.write_bytes(lines([make_review(case,output,[])]))
        self.assertEqual(self.run_cli().returncode,2)
        self.outputs.write_bytes(b""); self.assertEqual(self.run_cli().returncode,2)

    def test_json_errors_limits_and_closed_stdout(self):
        for raw in (b'\xef\xbb\xbf{}', b'\xff', b'{"id":"x","id":"y"}\n', b'{}\n\n',
                    b'{"id":"x","input":"\\ud800","expected_items":[]}\n', b'x'*(MAX_LINE_BYTES+1), b'x'*(MAX_FILE_BYTES+1)):
            with self.subTest(data=raw[:60]):
                self.cases.write_bytes(raw); result=self.run_cli()
                self.assertEqual((result.returncode,result.stdout),(2,b""))
        errors=io.StringIO()
        with patch.object(completeness,"load_inputs",side_effect=RuntimeError("PRIVATE_FAILURE")), patch.object(sys,"stderr",errors):
            self.assertEqual(completeness.main(["--cases",str(self.cases),"--outputs",str(self.outputs),"--reviews",str(self.reviews)]),2)
        self.assertNotIn("PRIVATE_FAILURE",errors.getvalue())
        self.cases.write_bytes(lines([{"id":"c"+str(i),"input":"","expected_items":[]} for i in range(1000)]))
        self.outputs.write_bytes(b""); self.reviews.write_bytes(b"")
        process=subprocess.Popen(self.command(),cwd=self.folder,env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        process.stdout.close(); process.stdout=None
        _,stderr=process.communicate(timeout=25)
        self.assertEqual(process.returncode,2,stderr)
        self.assertNotIn(b"Traceback",stderr)


if __name__ == "__main__":
    unittest.main()
