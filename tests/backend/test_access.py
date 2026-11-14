import os
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from datetime import timedelta

from connector_studio import main, models as m
from connector_studio.access import AccessError
from tests.security_support import SecurityBase, OWNER


class OwnerTests(SecurityBase):
    def test_owner_configuration_is_required(self):
        with patch.dict(os.environ,{"OWNER_ACCESS_TOKEN":"", "OWNER_ACCESS_TOKEN_FILE":"/nonexistent/test-secret"}):
            with self.assertRaises(RuntimeError): self.access.login(OWNER)

    def test_sessions_persist_are_opaque_and_logout_revokes(self):
        session = self.access.login(OWNER)
        self.engine.dispose()
        self.assertEqual(self.access.authenticate(session["token"])["actor"],"owner")
        with self.engine.connect() as conn:
            hashes = conn.execute(select(m.sessions.c.token_hash)).scalars().all()
        self.assertNotIn(session["token"],hashes)
        self.access.logout(session["token"])
        with self.assertRaises(AccessError): self.access.authenticate(session["token"])

    def test_rotation_invalidates_every_old_session(self):
        sessions = [self.access.login(OWNER) for _ in range(2)]
        os.environ["OWNER_ACCESS_TOKEN"] = "rotated-synthetic-owner-1234567890123456789"
        for session in sessions:
            with self.assertRaises(AccessError): self.access.authenticate(session["token"])
        self.assertEqual(self.access.authenticate(self.access.login(os.environ["OWNER_ACCESS_TOKEN"])["token"])["actor"],"owner")

    def test_session_expiry_and_csrf_are_server_side(self):
        session = self.access.login(OWNER)
        with self.assertRaises(AccessError): self.access.authenticate(session["token"],"wrong",True)
        self.access.authenticate(session["token"],session["csrf_token"],True)
        with self.engine.begin() as conn:
            conn.execute(update(m.sessions).values(expires_at=self.store.now(conn)-timedelta(seconds=1)))
        with self.assertRaises(AccessError): self.access.authenticate(session["token"])

    def test_failed_logins_are_rate_limited_without_credential_evidence(self):
        for _ in range(5):
            with self.assertRaises(AccessError): self.access.login("incorrect-synthetic")
        with self.assertRaises(AccessError) as raised: self.access.login(OWNER)
        self.assertEqual(raised.exception.status,429)
        with self.engine.connect() as conn:
            details = str(conn.execute(select(m.audit_events)).all())
        self.assertNotIn(OWNER,details)
        self.assertNotIn("incorrect-synthetic",details)

    def test_http_boundary_cookies_origin_csrf_logout_and_safe_errors(self):
        with patch.object(main,"database_engine",return_value=self.engine), TestClient(main.app,base_url="http://localhost") as client:
            self.assertEqual(client.get("/connections").status_code,401)
            self.assertEqual(client.post("/auth/login",json={"credential":OWNER}).status_code,403)
            response = client.post("/auth/login",headers={"Origin":"http://127.0.0.1:5173"},json={"credential":OWNER})
            self.assertEqual(response.status_code,200)
            self.assertIn("HttpOnly",response.headers["set-cookie"])
            self.assertIn("SameSite=strict",response.headers["set-cookie"])
            csrf = response.json()["csrf_token"]
            self.assertEqual(client.get("/auth/session").status_code,200)
            self.assertEqual(client.post("/auth/logout",headers={"Origin":"http://127.0.0.1:5173"}).status_code,403)
            invalid = client.post("/connections",headers={"Origin":"http://127.0.0.1:5173","X-CSRF-Token":csrf},json={"credential":OWNER})
            self.assertEqual(invalid.status_code,422)
            self.assertNotIn(OWNER,invalid.text)
            self.assertEqual(client.post("/auth/logout",headers={"Origin":"http://127.0.0.1:5173","X-CSRF-Token":csrf}).status_code,200)
            self.assertEqual(client.get("/connections").status_code,401)
            self.assertEqual(client.get("/health",headers={"Host":"attacker.example"}).status_code,400)

    def test_http_management_accepts_ids_and_never_exports_credential_values(self):
        connection,version,operation = self.connection(write=True)
        with self.engine.connect() as conn:
            connector = conn.scalar(select(m.versions.c.connector_id).where(m.versions.c.id==version))
        from tests.security_support import POLICY, SECRET
        with patch.object(main,"database_engine",return_value=self.engine), TestClient(main.app,base_url="http://localhost") as client:
            login = client.post("/auth/login",headers={"Origin":"http://127.0.0.1:5173"},json={"credential":OWNER})
            headers = {"Origin":"http://127.0.0.1:5173","X-CSRF-Token":login.json()["csrf_token"]}
            created = client.post("/connections",headers=headers,json={"connector_id":str(connector),"policy":POLICY,"credential":SECRET})
            self.assertEqual(created.status_code,201)
            self.assertNotIn(SECRET,created.text)
            listed = client.get("/connections")
            self.assertEqual(listed.status_code,200);self.assertNotIn(SECRET,listed.text)
            approved = client.post("/approvals",headers=headers,json={"connection_id":str(connection),"version_id":str(version),"operation":operation,"arguments":{}})
            self.assertEqual(approved.status_code,201)
            self.assertEqual(client.delete("/approvals/"+approved.json()["approval_id"],headers=headers).status_code,200)
            self.assertEqual(client.patch("/connections/"+created.json()["connection_id"],headers=headers,json={"revoke":True}).status_code,200)

    def test_http_login_body_is_bounded_before_validation(self):
        with patch.object(main,"database_engine",return_value=self.engine), TestClient(main.app,base_url="http://localhost") as client:
            response = client.post("/auth/login",headers={"Origin":"http://127.0.0.1:5173","Content-Type":"application/json"},content=b"x"*(2*1024*1024+1))
            self.assertEqual(response.status_code,413)
            self.assertEqual(response.json(),{"category":"request_too_large"})
