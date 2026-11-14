"""Trusted host-side coordinator; containers get no Docker socket or host mounts."""
import subprocess
import uuid

schema="probe_"+uuid.uuid4().hex

def compose(*args):
    subprocess.run(["docker","compose",*args],check=True)

def probe(mode):
    compose("exec","-T","api","python","-m","tests.durability_probe",mode,schema)

try:
    probe("seed")
    compose("restart","postgres","api","worker")
    compose("up","--wait","--wait-timeout","180")
    probe("check")
finally:
    probe("drop")
