"""Host-only scanner failure tests; synthetic results are never publication evidence."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools import verify_security as supervisor


class ScannerFailureTests(unittest.TestCase):
    def check(self, payload=None, timeout=False, raw=None):
        calls=[]
        def run(*args, **kwargs):
            calls.append(args)
            if args[:2]==("docker","inspect"):
                return SimpleNamespace(returncode=1,stdout="")
            if args[:2]==("docker","run"):
                if timeout: raise subprocess.TimeoutExpired(args,240)
                return SimpleNamespace(returncode=0,stdout=raw if raw is not None else json.dumps(payload))
            return SimpleNamespace(returncode=0,stdout="")
        with patch.object(supervisor,"run",side_effect=run):
            report=supervisor.dependency_scan()
        self.assertTrue(any(call[:3]==("docker","rm","--force") for call in calls))
        self.assertTrue(any(call[:2]==("docker","inspect") for call in calls))
        return report

    def test_timeout_retains_unavailable_and_cleans_up(self):
        self.assertEqual(self.check(timeout=True)["status"],"unavailable")

    def test_malformed_evidence_is_unavailable(self):
        self.assertEqual(self.check(raw="no usable report")["status"],"unavailable")

    def test_empty_skipped_and_vulnerable_evidence_block(self):
        for dependencies in ([],[{"skip_reason":"unavailable"}],[{"vulns":[{"id":"synthetic"}]}]):
            self.assertEqual(self.check({"dependencies":dependencies})["status"],"blocked")

    def test_successful_synthetic_scan_is_checked(self):
        self.assertEqual(self.check({"dependencies":[{"name":"synthetic","vulns":[]}]})["status"],"passed")


class EvidenceRetentionTests(unittest.TestCase):
    def test_new_check_preserves_the_previous_failure(self):
        with TemporaryDirectory() as directory, patch.object(supervisor,"REPORTS",Path(directory)):
            supervisor.save_report({"status":"blocked","failure":"synthetic failure"})
            supervisor.save_report({"status":"unavailable"})
            retained=list((Path(directory)/"history").glob("*.json"))
            self.assertEqual(len(retained),1)
            self.assertEqual(json.loads(retained[0].read_text())["failure"],"synthetic failure")
            self.assertEqual(json.loads((Path(directory)/"security.json").read_text())["status"],"unavailable")
