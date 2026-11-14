"""Structural checks run without credentials or a running stack."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = {**os.environ, "POSTGRES_PASSWORD": "configuration-test-only"}
        result = subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null", "config", "--format", "json"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError("Compose configuration could not be validated")
        cls.config = json.loads(result.stdout)

    def test_only_loopback_ports_are_published(self):
        for service in self.config["services"].values():
            for port in service.get("ports", []):
                self.assertEqual(port["host_ip"], "127.0.0.1")
        for name in ("postgres", "worker", "fixtures"):
            self.assertFalse(self.config["services"][name].get("ports"))

    def test_all_services_have_healthchecks(self):
        for service in self.config["services"].values():
            self.assertIn("healthcheck", service)

    def test_private_network_and_persistent_postgres(self):
        self.assertTrue(self.config["networks"]["private"]["internal"])
        for role in ("postgres", "worker", "fixtures"):
            self.assertEqual(set(self.config["services"][role]["networks"]), {"private"})
        self.assertIn("edge", self.config["services"]["api"]["networks"])
        self.assertEqual(set(self.config["services"]["web"]["networks"]), {"edge"})
        volume = self.config["services"]["postgres"]["volumes"][0]
        self.assertEqual(volume["type"], "volume")
        self.assertEqual(volume["target"], "/var/lib/postgresql/data")

    def test_backend_processes_are_separate_and_constrained(self):
        for role in ("api", "worker", "fixtures"):
            service = self.config["services"][role]
            self.assertEqual(service["environment"]["SERVICE_ROLE"], role)
            self.assertTrue(service["read_only"])
            self.assertIn("ALL", service["cap_drop"])
            self.assertIn("no-new-privileges:true", service["security_opt"])

    def test_missing_database_password_fails_closed(self):
        env = {key: value for key, value in os.environ.items() if key != "POSTGRES_PASSWORD"}
        result = subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null", "config", "--quiet"],
            cwd=ROOT, env=env, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_workspace_setup_contains_no_secrets(self):
        config = json.loads((ROOT / ".8090/environment.json").read_text())
        self.assertEqual(config["start"], "make dev")
        self.assertNotIn("POSTGRES_PASSWORD", json.dumps(config))
        self.assertTrue(config["terminals"])

    def test_secret_examples_are_empty(self):
        for line in (ROOT / ".env.example").read_text().splitlines():
            if line and not line.startswith("#"):
                self.assertEqual(line.split("=", 1)[1], "")

    def test_dependency_lockfiles_exist(self):
        self.assertIn("--hash=sha256:", (ROOT / "backend/requirements.txt").read_text())
        package = json.loads((ROOT / "frontend/package-lock.json").read_text())
        self.assertEqual(package["lockfileVersion"], 3)


if __name__ == "__main__":
    unittest.main()
