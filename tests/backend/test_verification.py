import hashlib
import json
import unittest
from connector_studio.verification import FIXED,LOCK,publication_blockers


class VerificationGateTests(unittest.TestCase):
    def report(self):
        return {"fixed_fixture_digest":hashlib.sha256(FIXED.read_bytes()).hexdigest(),
            "cases":{case["id"]:"passed" for case in json.loads(FIXED.read_text())["cases"]},
            "dependency_scan":{"status":"passed","backend_lock_digest":hashlib.sha256(LOCK.read_bytes()).hexdigest(),"report":{"dependencies":[{"name":"test","vulns":[]}]}},
            "adapter_contract_verified":True,"source_security_verified":True}

    def test_missing_evidence_fails_closed(self): self.assertTrue(publication_blockers({}))
    def test_only_provider_fixture_checks_do_not_pass_adapter_gate(self):
        value=self.report();value["adapter_contract_verified"]=False
        self.assertTrue(publication_blockers(value))
    def test_unavailable_and_failed_scan_block(self):
        for status in ("unavailable","blocked","pending"):
            value=self.report();value["dependency_scan"]["status"]=status
            self.assertTrue(publication_blockers(value))
    def test_missing_fixed_case_blocks(self):
        value=self.report();del value["cases"]["uncertain-write"]
        self.assertTrue(publication_blockers(value))
    def test_changed_fixture_and_pending_source_security_block(self):
        value=self.report();value["fixed_fixture_digest"]="changed"
        self.assertTrue(publication_blockers(value))
        value=self.report();value["source_security_verified"]=False
        self.assertTrue(publication_blockers(value))
    def test_stale_skipped_or_vulnerable_scan_blocks(self):
        value=self.report();value["dependency_scan"]["backend_lock_digest"]="stale"
        self.assertTrue(publication_blockers(value))
        for entry in ({"name":"dependency","skip_reason":"unavailable"}, {"name":"dependency","vulns":[{"id":"fixture-vulnerability"}]}):
            value=self.report();value["dependency_scan"]["report"]["dependencies"]=[entry]
            self.assertTrue(publication_blockers(value))

    def test_complete_synthetic_report_can_be_checked(self):
        # Unit test of the checker, NOT actual publication or generated verification.
        self.assertEqual(publication_blockers(self.report()),[])
