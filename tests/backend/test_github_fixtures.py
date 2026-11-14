"""Verify independent provider behavior; generated adapter acceptance follows in WO-6/7."""
import json
from pathlib import Path
import socket
import unittest
import urllib.request
import urllib.error
from jsonschema import Draft202012Validator, ValidationError

BASE="http://fixtures:8000"
TOKEN="Bearer simulator-github-token"


def request(path, method="GET", data=None, token=TOKEN, scenario="success", timeout=5):
    req=urllib.request.Request(BASE+path, method=method,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Authorization":token,"Content-Type":"application/json","X-Fixture-Scenario":scenario})
    try: response=urllib.request.urlopen(req,timeout=timeout)
    except urllib.error.HTTPError as error: response=error
    raw=response.read().decode()
    try: value=json.loads(raw)
    except json.JSONDecodeError: value=raw
    return response.status, value, response.headers


class GitHubFixtureTests(unittest.TestCase):
    def setUp(self): request("/_fixture/reset","POST",{})

    def test_mixed_pull_request_pages_and_pagination_are_independent(self):
        status, rows, headers=request("/repos/demo/connector-fixture/issues?page=1&per_page=2")
        self.assertEqual(status,200);self.assertEqual([row["number"] for row in rows],[1,2])
        self.assertIn("pull_request",rows[1]);self.assertIn('rel="next"',headers["Link"])
        self.assertEqual(headers["X-Connector-Simulator"],"github")
        status, rows, headers=request("/repos/demo/connector-fixture/issues?page=2&per_page=2")
        self.assertEqual([row["number"] for row in rows],[3,4]);self.assertNotIn("Link",headers)

    def test_get_and_create_have_real_synthetic_effects(self):
        self.assertEqual(request("/repos/demo/connector-fixture/issues/1")[1]["title"],"Demo issue 1")
        status, issue, _=request("/repos/demo/connector-fixture/issues","POST",{"title":"new"})
        self.assertEqual(status,201);self.assertEqual(issue["number"],5)
        self.assertEqual(request("/repos/demo/connector-fixture/issues/5")[1]["title"],"new")

    def test_native_failures_and_retry_after(self):
        path="/repos/demo/connector-fixture/issues/1"
        self.assertEqual(request(path,token="Bearer invalid")[0],401)
        self.assertEqual(request(path,scenario="permission")[0],403)
        self.assertEqual(request(path,scenario="upstream_error")[0],503)
        status,_,headers=request(path,scenario="rate_limit")
        self.assertEqual(status,429);self.assertEqual(headers["Retry-After"],"1")
        self.assertEqual(request("/repos/demo/connector-fixture/issues/999")[0],404)
        self.assertEqual(request("/repos/other/forbidden/issues")[0],404)

    def test_invalid_input_and_no_silent_coercion_in_public_contract(self):
        self.assertEqual(request("/repos/demo/connector-fixture/issues?per_page=101")[0],422)
        self.assertEqual(request("/repos/demo/connector-fixture/issues","POST",{"title":""})[0],422)
        schema=json.loads(Path("/app/contracts/github.schema.json").read_text())
        validator=Draft202012Validator({"$defs":schema["$defs"],"$ref":"#/$defs/GetInput"})
        with self.assertRaises(ValidationError): validator.validate({"owner":"demo","repo":"connector-fixture","issue_number":"1"})

    def test_documentation_and_provider_revision_switches_are_independent(self):
        request("/_fixture/reset","POST",{"docs_revision":"v2","provider_revision":"v1"})
        self.assertIn("headline",request("/_fixture/docs")[1])
        self.assertIn("title",request("/repos/demo/connector-fixture/issues/1")[1])
        request("/_fixture/reset","POST",{"docs_revision":"v1","provider_revision":"v2"})
        self.assertIn("Native issue fields: number integer, title",request("/_fixture/docs")[1])
        self.assertIn("headline",request("/repos/demo/connector-fixture/issues/1")[1])

    def test_malicious_documentation_remains_a_visible_data_fixture(self):
        request("/_fixture/reset","POST",{"docs_revision":"malicious"})
        self.assertIn("UNTRUSTED ATTACK FIXTURE",request("/_fixture/docs")[1])
        self.assertEqual(request("/_fixture/state")[1]["writes"],0)

    def test_ambiguous_write_fixture_has_effect_before_timeout(self):
        with self.assertRaises((TimeoutError,socket.timeout)):
            request("/repos/demo/connector-fixture/issues","POST",{"title":"uncertain"},scenario="ambiguous_write",timeout=0.2)
        self.assertEqual(request("/_fixture/state")[1]["writes"],1)
        self.assertEqual(request("/repos/demo/connector-fixture/issues/5")[1]["title"],"uncertain")

    def test_fixed_cases_cover_selected_behavior_and_valid_public_outputs(self):
        fixtures=json.loads(Path("/app/fixtures/github_acceptance.json").read_text())
        ids={case["id"] for case in fixtures["cases"]}
        self.assertTrue({"list-page-1","list-page-2","get-success","create-success","invalid-input","authentication-failure","missing-object","upstream-error","uncertain-write"}.issubset(ids))
        schema=json.loads(Path("/app/contracts/github.schema.json").read_text())
        Draft202012Validator({"$defs":schema["$defs"],"$ref":"#/$defs/ListOutput"}).validate(fixtures["cases"][0]["expected"])
