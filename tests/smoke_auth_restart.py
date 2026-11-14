"""Actual restart of real secure-file consumers; no credential values in host output."""
import subprocess
import uuid

schema = "security_probe_"+uuid.uuid4().hex

def compose(*args):
    subprocess.run(["docker","compose",*args],check=True)

def probe(mode):
    compose("exec","-T","api","python","-m","tests.auth_restart_probe",mode,schema)

try:
    probe("seed")
    compose("restart","postgres","api","worker")
    compose("up","--wait","--wait-timeout","180")
    probe("check")
finally:
    probe("drop")
print("Persisted owner sessions, encrypted connection secrets, source metadata/proposals/selections survived actual process/database restart with provisioned keys; no secret values displayed.")
