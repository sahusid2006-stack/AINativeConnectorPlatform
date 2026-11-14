"""Trusted host supervisor. No untrusted artifact receives Docker privileges."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
REPORTS=ROOT/".verification"
REPORTS.mkdir(exist_ok=True)
IMAGE="connector-studio-verifier"
NO_SWAP_PROVEN=False


def run(*args, timeout=240):
    return subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=timeout)


def save_report(evidence):
    latest=REPORTS/"security.json"
    if latest.exists():
        previous=latest.read_bytes()
        history=REPORTS/"history"
        history.mkdir(exist_ok=True)
        archived=history/(hashlib.sha256(previous).hexdigest()+".json")
        if not archived.exists():
            with archived.open("xb") as handle:
                handle.write(previous)
        else:
            assert archived.read_bytes()==previous, "Retained evidence was altered"
    latest.write_text(json.dumps(evidence,indent=2)+"\n")


def sandbox(mode="probe", deadline=10, memory="256m"):
    global NO_SWAP_PROVEN
    name="connector-check-"+uuid.uuid4().hex
    command=["docker","run","--name",name,"--network","none","--read-only","--user","10001:10001",
        "--cap-drop","ALL","--security-opt","no-new-privileges:true","--cpus","1","--memory",memory,
        "--memory-swap",memory,"--pids-limit","64","--tmpfs","/tmp:rw,noexec,nosuid,size=16m",IMAGE,
        "python","/app/tests/security_probe.py"]
    if mode!="probe": command.append(mode)
    result=None
    timed_out=False
    try:
        try: result=run(*command,timeout=deadline)
        except subprocess.TimeoutExpired: timed_out=True
        inspected=run("docker","inspect",name)
        assert inspected.returncode==0, "sandbox evidence is unavailable"
        config=json.loads(inspected.stdout)[0]
        host=config["HostConfig"]
        assert host["NetworkMode"]=="none"
        assert host["ReadonlyRootfs"]
        assert host["Memory"]==(256 if memory=="256m" else 64)*1024*1024
        assert host["NanoCpus"]==1000000000 and host["PidsLimit"]==64
        assert not host.get("Binds")
        assert all(mount["Type"]=="tmpfs" and mount["Destination"]=="/tmp" for mount in config["Mounts"])
        if mode=="probe":
            assert result and result.returncode==0, "boundary probe failed"
            proof=json.loads(result.stdout)
            assert proof["limits"]["memory_bytes"]==host["Memory"] and proof["limits"]["pids"]==64
            NO_SWAP_PROVEN=not proof["swap_available"]
            if host["MemorySwap"]!=host["Memory"]:
                assert NO_SWAP_PROVEN, "Swap limits are unsupported on a host with swap; runner must stay blocked"
            proof["swap_limit_mode"]="explicit" if host["MemorySwap"]==host["Memory"] else "no_swap_available"
            return proof
        assert host["MemorySwap"]==host["Memory"] or NO_SWAP_PROVEN
        if mode=="spin":
            assert timed_out, "supervisor must stop an unbounded runner"
            return {"status":"passed","check":"deadline"}
        assert config["State"]["OOMKilled"] and result.returncode!=0, "memory cap must actually kill excessive allocation"
        return {"status":"passed","check":"memory_enforced"}
    finally:
        # Even an exceeded deadline must not leave a process/container running.
        run("docker","rm","--force",name,timeout=10)
        assert run("docker","inspect",name).returncode!=0, "sandbox cleanup failed"


def dependency_scan():
    started=time.time()
    name="connector-scan-"+uuid.uuid4().hex
    try:
        try:
            result=run("docker","run","--name",name,"--rm",IMAGE,"python","-m","pip_audit","--format","json","--progress-spinner","off",timeout=240)
            report=json.loads(result.stdout)
            dependencies=report["dependencies"]
            # Conservative threshold: block every vulnerability, including tool dependencies.
            status="passed" if result.returncode==0 and dependencies and all(not d.get("vulns") and not d.get("skip_reason") for d in dependencies) else "blocked"
        except (subprocess.TimeoutExpired,ValueError,KeyError):
            report={"diagnostic":"Dependency scan timed out or did not return usable evidence"}
            status="unavailable"
    finally:
        run("docker","rm","--force",name,timeout=10)
        assert run("docker","inspect",name).returncode!=0, "scanner cleanup failed"
    return {"status":status,"duration_seconds":round(time.time()-started,2),"report":report,
        "backend_lock_digest":hashlib.sha256((ROOT/"backend/requirements.txt").read_bytes()).hexdigest()}


def main():
    evidence={"status":"blocked", "checked_at":datetime.now(timezone.utc).isoformat(),
              "isolation":[], "dependency_scan":{"status":"pending"},
              "fixed_fixture_digest":hashlib.sha256((ROOT/"fixtures/github_acceptance.json").read_bytes()).hexdigest(),
              "scope":"Runner boundaries and dependencies only; source-fetch and generated-adapter security gates remain pending"}
    def retain():
        save_report(evidence)
    retain()  # Never leave a previous passing report after a new failed probe.
    try:
        for mode,deadline,memory in (("probe",10,"256m"),("spin",2,"256m"),("memory",20,"64m")):
            evidence["isolation"].append(sandbox(mode,deadline,memory))
            retain()
        evidence["dependency_scan"]=dependency_scan()
        evidence["status"]=evidence["dependency_scan"]["status"]
        retain()
    except Exception:
        evidence["failure"]="Boundary or scanner verification failed; publication remains blocked"
        retain()
        raise
    print("Isolated runner: non-root, no ambient credentials/host mounts/network, immutable fixtures, CPU/memory/PID controls, deadline and cleanup verified.")
    print("Dependency scan: "+evidence["dependency_scan"]["status"]+". Evidence: .verification/security.json")
    if evidence["status"]!="passed": raise SystemExit("Publication remains blocked: dependency security evidence failed or is unavailable")


if __name__=="__main__":
    if sys.argv[1:]==["--prepare"]:
        save_report({"status":"unavailable", "dependency_scan":{"status":"unavailable"}, "diagnostic":"Verification has not completed"})
    else:
        main()
