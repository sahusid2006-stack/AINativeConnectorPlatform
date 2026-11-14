"""Verify only startup plumbing, never connector behavior or model generation."""
import argparse
import json
import uuid
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def compose(*args):
    return subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout


def verify_restart():
    table = "setup_probe_" + uuid.uuid4().hex
    def sql(statement):
        return compose("exec", "-T", "postgres", "psql", "-U", "connector_studio",
                       "-d", "connector_studio", "-v", "ON_ERROR_STOP=1", "-Atc", statement)
    sql(f"CREATE TABLE {table} (value integer); INSERT INTO {table} VALUES (1)")
    try:
        compose("restart", "postgres")
        compose("up", "--wait", "--wait-timeout", "180")
        assert sql(f"SELECT value FROM {table}").strip() == "1", "Database data must survive restart"
    finally:
        sql(f"DROP TABLE IF EXISTS {table}")
    print("PostgreSQL restart persistence probe passed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true", help="Also restart PostgreSQL and check a disposable probe")
    args = parser.parse_args()
    if args.restart:
        verify_restart()
    for role in ("api", "worker", "fixtures"):
        result = compose(
            "exec", "-T", role, "python", "-c",
            "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())",
        )
        health = json.loads(result)
        assert health["service"] == role
        assert health["status"] == "ready"
        assert health["stage"] == ("simulator-fixtures" if role == "fixtures" else "infrastructure-only")
        assert health["product_features"] == ("github_simulator_only" if role == "fixtures" else "not_implemented")
    with urllib.request.urlopen("http://127.0.0.1:5173/api/health", timeout=10) as response:
        assert json.load(response)["service"] == "api", "Web proxy must reach the real API"
    with urllib.request.urlopen("http://127.0.0.1:5173", timeout=10) as response:
        assert b"Connector Studio" in response.read()
    for path in ("/connectors", "/invoke"):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=10)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("Bootstrap must not expose product endpoints")
    compose("run", "--rm", "--no-deps", "web", "npm", "run", "build")
    compose("exec", "-T", "api", "pip", "check")
    print("Setup smoke checks passed: process health, PostgreSQL, same-origin proxy, web build, dependency consistency.")
    print("Connector generation, protocol compatibility, and provider verification were NOT tested.")


if __name__ == "__main__":
    main()
