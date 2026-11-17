import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

FOLDER=Path(__file__).resolve().parent
sys.path.insert(0,str(FOLDER))
spec=importlib.util.spec_from_file_location("rag_expanded_ollama_local_run",FOLDER/"rag_expanded_ollama_local_run.py")
check=importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ExpandedOllamaTests(unittest.TestCase):
    def setUp(self):
        root=check.ROOT/"04-evals/examples"
        self.paths=[root/name for name in check.INPUTS.values()]
        cases,corpus,expectations,ids=check.load_inputs(*self.paths)
        self.attempts,self.cases,self.controls=check.build_plan(cases,corpus,expectations,ids)

    def test_plan_has_unique_ordered_attempts_without_simulated_fixtures(self):
        self.assertEqual((len(self.attempts),len(self.cases),len(self.controls)),(24,24,8))
        self.assertEqual([x["plan"]["scenario_id"] for x in self.attempts],list(check.expanded.SCENARIO_IDS)*2)
        self.assertEqual([x["plan"]["repetition"] for x in self.attempts],[1]*12+[2]*12)
        self.assertEqual(len({x["plan"]["id"] for x in self.attempts}),24)
        self.assertTrue(all("fixture" not in item and "fixture_line_sha256" not in item["plan"] for item in self.attempts))
        rows=[{json.loads(line)["id"]:hashlib.sha256(line).hexdigest() for line in path.read_bytes().splitlines()} for path in self.paths]
        for item in self.attempts:
            plan=item["plan"]; scenario=plan["scenario_id"]
            self.assertEqual(plan["scenario_line_sha256"],rows[0][scenario])
            self.assertEqual(plan["corpus_line_sha256"],rows[1][scenario])
            self.assertEqual(plan["expectation_line_sha256"],rows[2].get(scenario))

    def test_exposure_is_not_a_security_judgment(self):
        item=self.attempts[0]
        out={"id":item["case"]["id"],"status":"answered","answer":"respuesta", "sources":item["documents"],"sources_complete":True}
        exposed,raw=check.validate_output(item,out,["synthetic_secret_"+"x"*32])
        self.assertTrue(exposed)
        self.assertEqual(json.loads(raw)["answer"],"respuesta")
        partial=dict(out,sources=item["documents"][:1])
        self.assertFalse(check.validate_output(item,partial,[])[0])
        empty=dict(out,status="no_context",sources=[])
        self.assertFalse(check.validate_output(item,empty,[])[0])

    def test_rejects_foreign_source_and_credentials(self):
        item=self.attempts[0]
        out={"id":item["case"]["id"],"status":"answered","answer":"respuesta", "sources":copy.deepcopy(item["documents"]),"sources_complete":True}
        out["sources"][0]["text"]="dato alterado"
        with self.assertRaises(ValueError): check.validate_output(item,out,[])
        out["sources"]=copy.deepcopy(item["documents"])
        out["answer"]="synthetic_secret_"+"x"*32
        with self.assertRaises(ValueError): check.validate_output(item,out,[out["answer"]])

    def test_invalid_corpus_rejected_before_run(self):
        with tempfile.TemporaryDirectory(prefix="expanded-real-preflight-") as raw:
            paths=[]
            for path in self.paths:
                dest=Path(raw)/path.name; dest.write_bytes(path.read_bytes()); paths.append(dest)
            rows=[json.loads(line) for line in paths[1].read_bytes().splitlines()]
            rows[1]["documents"][1]["text"]="fuente modificada"
            paths[1].write_bytes(b"".join(check._encode(row)+b"\n" for row in rows))
            with self.assertRaises(check.InputError): check.load_inputs(*paths)

    def test_failure_in_second_round_retains_pending_attempts(self):
        records=check.orchestration.initial_records(self.attempts)
        with tempfile.TemporaryDirectory(prefix="expanded-real-execute-") as raw:
            output,events=Path(raw)/"output",Path(raw)/"events"
            output.write_bytes(b""); events.write_bytes(b"")
            def work(item,params,initialized,record,output_path):
                record["api_cleanup"]="confirmed"; record["capture_started"]=True
                if item["plan"]["id"]=="f2_a_r2":
                    record["stage"]="api_answer_capture"
                    raise RuntimeError("private error")
                value={"id":item["case"]["id"],"status":"answered","answer":"ficticia",
                       "sources":item["documents"],"sources_complete":True}
                check.expanded._append(output_path,check._encode(value)+b"\n")
                record["output_saved"]=True
            with patch.object(check.orchestration,"exercise_attempt",side_effect=work):
                out_ok,event_ok,_=check.orchestration.execute(self.attempts,records,{},output,events)
            self.assertEqual((out_ok,event_ok),("confirmed","confirmed"))
            self.assertEqual([r["status"] for r in records[:17]],["completed"]*16+["failed"])
            self.assertTrue(all(row["status"]=="not_started" for row in records[17:]))
            self.assertNotIn("private error",events.read_text())
            self.assertEqual(json.loads(output.read_bytes().splitlines()[-1])["status"],"error")


if __name__=="__main__":
    unittest.main()
