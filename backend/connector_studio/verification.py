"""Fail-closed report checks for the future registry; these do not publish a version."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FIXED=ROOT/"fixtures/github_acceptance.json"
LOCK=ROOT/"requirements.txt"


def publication_blockers(report):
    fixed=json.loads(FIXED.read_text())
    expected={case["id"] for case in fixed["cases"]}
    blockers=[]
    if report.get("fixed_fixture_digest")!=hashlib.sha256(FIXED.read_bytes()).hexdigest():
        blockers.append("Independent fixture evidence is missing or stale")
    cases=report.get("cases",{})
    if any(cases.get(case)!="passed" for case in expected):
        blockers.append("Required fixed contract cases are missing or failed")
    scan=report.get("dependency_scan",{})
    dependencies=scan.get("report",{}).get("dependencies",[])
    if (scan.get("status")!="passed"
        or scan.get("backend_lock_digest")!=hashlib.sha256(LOCK.read_bytes()).hexdigest()
        or not dependencies
        or any(item.get("vulns") or item.get("skip_reason") for item in dependencies)):
        blockers.append("Dependency security evidence is missing, unavailable, or failed")
    if report.get("adapter_contract_verified") is not True:
        blockers.append("Generated adapter acceptance has not run")
    if report.get("source_security_verified") is not True:
        blockers.append("Source-fetch and instruction-injection boundaries have not been verified")
    return blockers
