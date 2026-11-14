"""Exercise actual worker loss/recovery, not merely repository timestamp checks."""
import json
import subprocess
import time
import urllib.error
import urllib.request


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True)


def health():
    try:
        response = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


try:
    compose("stop", "worker")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status, result = health()
        if result["status"] == "degraded":
            assert status == 503 and result["workers"]["worker_process"] == "degraded"
            assert result["workers"]["scheduler"] == "not_implemented"
            assert result["job_processing"] == "not_implemented"
            break
        time.sleep(1)
    else:
        raise AssertionError("Worker loss must degrade actual API health after its heartbeat expires")
finally:
    compose("up", "-d", "worker")
    compose("up", "--wait", "--wait-timeout", "180")
assert health()[0] == 200, "Worker recovery must restore readiness"
print("Actual worker stop degraded API health; worker restart restored process readiness, not job-processing readiness.")
