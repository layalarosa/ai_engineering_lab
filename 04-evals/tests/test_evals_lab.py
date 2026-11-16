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
from evals_lab import __main__ as cli
from evals_lab.cases import InputError, MAX_FILE_BYTES, MAX_LINE_BYTES, load_cases, load_outputs


def line(record):
    return json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii")


class EvalsLabTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="evals-lab-test-")
        self.folder = Path(self.tempdir.name).resolve()
        self.assertEqual(self.folder.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.folder.name.startswith("evals-lab-test-"))
        self.addCleanup(self.tempdir.cleanup)
        self.cases_path = self.folder / "private-cases-name.jsonl"
        self.outputs_path = self.folder / "private-outputs-name.jsonl"
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(ROOT / "src")
        self.env["PYTHONUTF8"] = "1"

    def files(self, cases, outputs):
        self.cases_path.write_bytes(cases)
        self.outputs_path.write_bytes(outputs)

    def run_cli(self, extra=()):
        return subprocess.run([sys.executable, "-S", "-m", "evals_lab", "--cases", str(self.cases_path),
                               "--outputs", str(self.outputs_path), *extra], cwd=self.folder,
                              env=self.env, capture_output=True, timeout=20)

    def example(self):
        cases = b"\n".join(map(line, [
            {"id": "first", "input": "PRIVATE_INPUT", "expected_output": "PRIVATE_EXPECTED"},
            {"id": "second", "input": "x", "expected_output": "OK"},
            {"id": "missing", "input": "x", "expected_output": "x"},
            {"id": "empty", "input": "", "expected_output": ""},
        ])) + b"\n"
        outputs = b"\r\n".join(map(line, [
            {"id": "empty", "output": ""}, {"id": "second", "output": "no"},
            {"id": "first", "output": "PRIVATE_EXPECTED"},
        ])) + b"\r\n"
        self.files(cases, outputs)
        return cases, outputs

    def test_report_separates_pass_fail_missing_and_does_not_expose_text(self):
        cases, outputs = self.example()
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertEqual({key: report[key] for key in ("total", "passed", "failed", "errors", "valid_responses")},
                         {"total": 4, "passed": 2, "failed": 1, "errors": 1, "valid_responses": 3})
        self.assertEqual(report["exact_match_valid"], 2 / 3)
        self.assertEqual(report["pass_rate_all"], 0.5)
        self.assertEqual(report["dataset_sha256"], hashlib.sha256(cases).hexdigest())
        self.assertEqual(report["outputs_sha256"], hashlib.sha256(outputs).hexdigest())
        self.assertEqual(report["cases"], [
            {"id": "first", "status": "passed", "error_code": None},
            {"id": "second", "status": "failed", "error_code": None},
            {"id": "missing", "status": "error", "error_code": "missing_response"},
            {"id": "empty", "status": "passed", "error_code": None},
        ])
        self.assertNotIn(b"PRIVATE_INPUT", result.stdout + result.stderr)
        self.assertNotIn(b"PRIVATE_EXPECTED", result.stdout + result.stderr)
        expected_bytes = (json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False) + "\n").encode("ascii")
        self.assertEqual(result.stdout, expected_bytes)
        self.assertEqual(self.run_cli().stdout, result.stdout)

    def test_all_pass_including_empty_and_escaped_nul(self):
        values = ["", "a\x00b\n😀"]
        cases = [{"id": f"c{i}", "input": "", "expected_output": value} for i, value in enumerate(values)]
        outputs = [{"id": f"c{i}", "output": value} for i, value in reversed(list(enumerate(values)))]
        self.files(b"\n".join(map(line, cases)), b"\n".join(map(line, outputs)))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["passed"], 2)
        self.assertEqual(report["exact_match_valid"], 1.0)
        self.assertEqual(report["pass_rate_all"], 1.0)

    def test_empty_outputs_have_null_conditional_accuracy_and_error_exit(self):
        self.files(line({"id": "x", "input": "", "expected_output": ""}) + b"\n", b"")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertIsNone(report["exact_match_valid"])
        self.assertEqual(report["valid_responses"], 0)
        self.assertEqual(report["pass_rate_all"], 0.0)
        self.assertEqual(report["cases"][0]["error_code"], "missing_response")

    def test_exact_comparison_does_not_normalize_unicode_or_whitespace(self):
        cases = [{"id": "unicode", "input": "", "expected_output": "é"},
                 {"id": "spaces", "input": "", "expected_output": "OK "},
                 {"id": "case", "input": "", "expected_output": "OK"}]
        outputs = [{"id": "unicode", "output": "e\u0301"}, {"id": "spaces", "output": "OK"},
                   {"id": "case", "output": "ok"}]
        self.files(b"\n".join(map(line, cases)), b"\n".join(map(line, outputs)))
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["failed"], 3)

    def test_invalid_inputs_fail_before_report_without_leaking_paths_or_content(self):
        valid_case = line({"id": "ok", "input": "PRIVATE_TEXT", "expected_output": "x"})
        valid_output = line({"id": "ok", "output": "x"})
        invalid_pairs = [
            (b"", b""),
            (b"\xef\xbb\xbf" + valid_case, valid_output),
            (valid_case + b"\n\n", valid_output),
            (valid_case + b"\r", valid_output),
            (b"\xff", valid_output),
            (b'{"id":"ok","input":"PRIVATE_TEXT","expected_output":', valid_output),
            (b'{"id":"ok","id":"other","input":"PRIVATE_TEXT","expected_output":"x"}', valid_output),
            (line({"id": "Bad", "input": "", "expected_output": "x"}), valid_output),
            (line({"id": "ok", "input": "\ud800", "expected_output": "x"}), valid_output),
            (line({"id": "ok", "input": "", "expected_output": "x", "extra": "secret"}), valid_output),
            (line({"id": "ok", "input": "", "expected_output": 7}), valid_output),
            (valid_case + b"\n" + valid_case, valid_output),
            (valid_case, valid_output + b"\n" + valid_output),
            (valid_case, line({"id": "unknown", "output": "secret"})),
            (valid_case, b'{"id":"ok","output":NaN}'),
        ]
        for cases, outputs in invalid_pairs:
            with self.subTest(cases=repr(cases[:80]), outputs=repr(outputs[:80])):
                self.files(cases, outputs)
                result = self.run_cli()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"PRIVATE_TEXT", result.stderr)
                self.assertNotIn(b"private-cases-name", result.stderr)
                self.assertNotIn(b"private-outputs-name", result.stderr)

    def test_missing_file_and_unknown_arguments_are_sanitized(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"private-cases-name", result.stderr)
        result = self.run_cli(("--secret-argument", "PRIVATE_VALUE"))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"PRIVATE_VALUE", result.stderr)
        self.assertNotIn(b"private-cases-name", result.stderr)

    def test_record_and_text_limits(self):
        case = {"id": "ok", "input": "x" * 4000, "expected_output": "y" * 4000}
        self.files(line(case), line({"id": "ok", "output": "y" * 4000}))
        self.assertEqual(self.run_cli().returncode, 0)
        case["input"] += "x"
        self.files(line(case), b"")
        self.assertEqual(self.run_cli().returncode, 2)
        records = [line({"id": f"case_{i}", "input": "", "expected_output": ""}) for i in range(1001)]
        self.files(b"\n".join(records[:1000]), b"")
        self.assertEqual(self.run_cli().returncode, 1)
        self.files(b"\n".join(records), b"")
        self.assertEqual(self.run_cli().returncode, 2)

    def test_identifier_and_output_boundaries(self):
        long_id = "a" * 64
        case = {"id": long_id, "input": "", "expected_output": "x"}
        self.files(line(case), line({"id": long_id, "output": "x"}))
        self.assertEqual(self.run_cli().returncode, 0)
        self.files(line({**case, "id": long_id + "a"}), b"")
        self.assertEqual(self.run_cli().returncode, 2)
        self.files(line({**case, "expected_output": "x" * 4001}), b"")
        self.assertEqual(self.run_cli().returncode, 2)
        self.files(line(case), line({"id": long_id, "output": "x" * 4001}))
        self.assertEqual(self.run_cli().returncode, 2)
        self.files(line(case), line({"id": long_id, "output": "\udfff"}))
        self.assertEqual(self.run_cli().returncode, 2)

    def test_line_and_file_byte_boundaries_with_crlf(self):
        record = line({"id": "ok", "input": "", "expected_output": ""})
        exact_line = record + b" " * (MAX_LINE_BYTES - len(record))
        self.files(exact_line + b"\r\n", b"")
        self.assertEqual(self.run_cli().returncode, 1)
        self.files(exact_line + b" " + b"\r\n", b"")
        self.assertEqual(self.run_cli().returncode, 2)
        lines = []
        for i in range(32):
            data = line({"id": f"case_{i}", "input": "", "expected_output": ""})
            lines.append(data + b" " * (65_535 - len(data)) + b"\n")
        payload = b"".join(lines)
        self.assertEqual(len(payload), MAX_FILE_BYTES)
        self.files(payload, b"")
        self.assertEqual(self.run_cli().returncode, 1)
        self.files(payload + b"x", b"")
        self.assertEqual(self.run_cli().returncode, 2)

    def test_output_limits_duplicates_and_empty_lines(self):
        valid_case = line({"id": "ok", "input": "", "expected_output": "x"})
        cases = [line({"id": f"c{i}", "input": "", "expected_output": ""}) for i in range(1000)]
        outputs = [line({"id": f"c{i}", "output": ""}) for i in range(1000)]
        self.files(b"\n".join(cases), b"\n".join(outputs))
        self.assertEqual(self.run_cli().returncode, 0)
        self.files(b"\n".join(cases), b"\n".join(outputs + [line({"id": "extra", "output": ""})]))
        self.assertEqual(self.run_cli().returncode, 2)
        self.files(valid_case, b"\n")
        self.assertEqual(self.run_cli().returncode, 2)
        self.files(valid_case, b'{"id":"ok","output":"x","output":"y"}')
        self.assertEqual(self.run_cli().returncode, 2)

    def test_reordered_responses_are_joined_by_id(self):
        self.example()
        original = json.loads(self.run_cli().stdout)
        reversed_lines = list(reversed(self.outputs_path.read_bytes().splitlines()))
        self.outputs_path.write_bytes(b"\n".join(reversed_lines))
        reordered = json.loads(self.run_cli().stdout)
        self.assertEqual(original["cases"], reordered["cases"])
        self.assertEqual(original["passed"], reordered["passed"])
        self.assertNotEqual(original["outputs_sha256"], reordered["outputs_sha256"])

    def test_loader_rejects_invalid_data_without_dependency_on_cli(self):
        self.files(line({"id": "ok", "input": "", "expected_output": ""}), b"")
        cases, digest = load_cases(self.cases_path)
        outputs, outputs_digest = load_outputs(self.outputs_path)
        self.assertEqual(len(cases), 1)
        self.assertEqual(outputs, [])
        self.assertEqual(digest, hashlib.sha256(self.cases_path.read_bytes()).hexdigest())
        self.assertEqual(outputs_digest, hashlib.sha256(b"").hexdigest())
        self.cases_path.write_bytes(b'{}')
        with self.assertRaises(InputError):
            load_cases(self.cases_path)

    def test_unexpected_error_is_sanitized_and_returns_two(self):
        self.example()
        errors = io.StringIO()
        with patch.object(cli, "load_cases", side_effect=RuntimeError("PRIVATE_FAILURE")), patch.object(sys, "stderr", errors):
            code = cli.main(["--cases", str(self.cases_path), "--outputs", str(self.outputs_path)])
        self.assertEqual(code, 2)
        self.assertNotIn("PRIVATE_FAILURE", errors.getvalue())
        self.assertNotIn("private-cases-name", errors.getvalue())

    def test_closed_stdout_fails_without_traceback(self):
        cases = [line({"id": f"c{i}", "input": "", "expected_output": ""}) for i in range(1000)]
        self.files(b"\n".join(cases), b"")
        process = subprocess.Popen([sys.executable, "-S", "-m", "evals_lab", "--cases", str(self.cases_path),
                                    "--outputs", str(self.outputs_path)], cwd=self.folder, env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdout.close()
        process.stdout = None
        _, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 2, stderr)
        self.assertNotIn(b"Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
