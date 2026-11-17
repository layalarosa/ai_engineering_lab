import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0,str(FOLDER))
spec=importlib.util.spec_from_file_location("rag_expanded_postgres_fake_run",FOLDER/"rag_expanded_postgres_fake_run.py")
check=importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ExpandedRagTests(unittest.TestCase):
    def setUp(self):
        examples=check.ROOT/"04-evals/examples"
        paths=[examples/name for name in check.INPUTS.values()]
        cases,corpus,expectations,fixtures,ids=check.expanded.load_inputs(*paths)
        self.attempts,_,_=check.expanded.build_plan(cases,corpus,expectations,fixtures,ids)
        self.records=check.initial_records(self.attempts)
        self.temp=tempfile.TemporaryDirectory(prefix="rag-expanded-test-")
        self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name)/"outputs"
        self.events=Path(self.temp.name)/"events"
        self.output.write_bytes(b""); self.events.write_bytes(b"")

    def test_generator_requires_exact_context_in_all_scenarios(self):
        for item in self.attempts[:12]:
            docs={s["document_id"]:s["text"] for s in item["documents"]}
            received=[]
            generate=check.five.generation_for(item["case"],docs,item["fixture"],received)
            fragments=[{"etiqueta":f"[{i}]","texto":text} for i,text in enumerate(docs.values(),1)]
            prompt={"pregunta":item["case"]["input"],"fragmentos":fragments}
            args={"model":"fake-generation","system":check.answer.SYSTEM,"base_url":check.one.URL}
            with self.subTest(scenario=item["plan"]["scenario_id"]):
                self.assertEqual(generate(json.dumps(prompt),**args),item["fixture"]["answer"])
                self.assertEqual(received,[fragments])
                prompt["fragmentos"]=fragments[:-1]
                with self.assertRaises(ValueError): generate(json.dumps(prompt),**args)

    def test_failure_in_second_round_stops_and_preserves_order(self):
        def exercise(item,params,initialized,record,output):
            record["api_cleanup"]="confirmed"
            record["capture_started"]=True
            if item["plan"]["id"]=="f2_a_r2":
                record["stage"]="api_answer_capture"
                raise ValueError("private failure")
            result=dict(item["fixture"],id=item["case"]["id"])
            check.expanded._append(output,check._encode(result)+b"\n")
            record["output_saved"]=True
        with patch.object(check,"exercise_attempt",side_effect=exercise) as work:
            out_ok,event_ok,_=check.execute(self.attempts,self.records,{},self.output,self.events)
        self.assertEqual((out_ok,event_ok,work.call_count),("confirmed","confirmed",17))
        self.assertEqual([r["status"] for r in self.records[:17]],["completed"]*16+["failed"])
        self.assertTrue(all(r["status"]=="not_started" for r in self.records[17:]))
        self.assertEqual(self.records[16]["id"],"f2_a_r2")
        outputs=[json.loads(raw) for raw in self.output.read_bytes().splitlines()]
        self.assertEqual((len(outputs),outputs[-1]["status"]),(17,"error"))
        self.assertNotIn("private failure",self.events.read_text())

    def test_partial_write_not_retried(self):
        def exercise(item,params,initialized,record,output):
            record["api_cleanup"]="confirmed"
            record["output_write_started"]=True
            output.write_bytes(b"partial")
            raise OSError("incomplete")
        with patch.object(check,"exercise_attempt",side_effect=exercise):
            out_ok,event_ok,_=check.execute(self.attempts,self.records,{},self.output,self.events)
        self.assertEqual((out_ok,event_ok),("unconfirmed","confirmed"))
        self.assertEqual(self.output.read_bytes(),b"partial")
        self.assertEqual((self.records[0]["error"],self.records[1]["status"]),("output_write_unconfirmed","not_started"))

    def test_unconfirmed_api_cleanup_stops_batch(self):
        def exercise(item,params,initialized,record,output):
            record["api_cleanup"]="unconfirmed"
        with patch.object(check,"exercise_attempt",side_effect=exercise):
            check.execute(self.attempts,self.records,{},self.output,self.events)
        self.assertEqual((self.records[0]["error"],self.records[1]["status"]),("cleanup_unconfirmed","not_started"))

    def test_event_failure_never_starts_work(self):
        original=check.expanded._append
        def append(path,data):
            if path==self.events: raise OSError("event failure")
            return original(path,data)
        with patch.object(check.expanded,"_append",side_effect=append),patch.object(check,"exercise_attempt") as work:
            out_ok,event_ok,_=check.execute(self.attempts,self.records,{},self.output,self.events)
        work.assert_not_called()
        self.assertEqual((out_ok,event_ok,self.records[0]["capture_started"]),("confirmed","unconfirmed",False))
        self.assertEqual(self.records[1]["status"],"not_started")


if __name__ == "__main__":
    unittest.main()
