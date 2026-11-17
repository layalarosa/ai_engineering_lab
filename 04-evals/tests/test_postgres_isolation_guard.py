import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).with_name("postgres_isolation_check.py")
SPEC = importlib.util.spec_from_file_location("postgres_isolation_check", PATH)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)

RUN = "a" * 32
NAME = "aielab-security-" + RUN
CID = "b" * 64


def result(data=b"", code=0):
    return subprocess.CompletedProcess([], code, data, b"")


def owned(label=RUN, name=NAME, cid=CID):
    return {"Name": "/" + name, "Id": cid, "Config": {"Labels": {check.LABEL: label}}}


class OwnershipTests(unittest.TestCase):
    def test_deletes_only_verified_container_id(self):
        responses = [result((CID + "\n").encode()), result(json.dumps([owned()]).encode()), result(), result()]
        with patch.object(check, "docker", side_effect=responses) as docker:
            self.assertEqual(check.cleanup(NAME, RUN), "confirmed_removed")
        self.assertEqual(docker.call_args_list[2].args, ("rm", "-f", "-v", CID))

    def test_mismatch_and_inspection_error_never_delete(self):
        for inspect_result in (result(json.dumps([owned(label="wrong")]).encode()),
                               result(json.dumps([owned(cid="c" * 64)]).encode()), result(code=1)):
            with self.subTest(inspect=inspect_result.stdout), patch.object(check, "docker", side_effect=[result((CID + "\n").encode()), inspect_result]) as docker:
                self.assertEqual(check.cleanup(NAME, RUN), "unconfirmed")
                self.assertFalse(any(call.args[0] == "rm" for call in docker.call_args_list))

    def test_no_container_and_ambiguous_results(self):
        with patch.object(check, "docker", return_value=result()):
            self.assertEqual(check.cleanup(NAME, RUN), "confirmed_absent")
        with patch.object(check, "docker", return_value=result((CID + "\n" + "c" * 64 + "\n").encode())) as docker:
            self.assertEqual(check.cleanup(NAME, RUN), "unconfirmed")
            self.assertEqual(docker.call_count, 1)


if __name__ == "__main__":
    unittest.main()
