"""Synthetic credentials and disposable-schema helpers, never application-table resets."""
import base64
import os
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import event, text, update, delete
from connector_studio.database import database_engine, migrate
from connector_studio.repositories import EvidenceStore
from connector_studio import models as m
from connector_studio.access import OwnerAccess
from connector_studio.policy import ConnectionSecurity
from tests.test_durability import package

OWNER = "synthetic-owner-test-value-123456789012345"
SECRET = "synthetic-provider-secret-123456789012345"
POLICY = {"destinations":["http://fixtures:8000"], "repositories":["demo/connector-fixture"]}
RESOURCES = {"destination":"http://fixtures:8000", "repository":"demo/connector-fixture"}


class SecurityBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = patch.dict(os.environ, {"OWNER_ACCESS_TOKEN":OWNER,
            "SECRET_ENCRYPTION_KEY":base64.urlsafe_b64encode(b"s"*32).decode()})
        cls.config.start()
        cls.admin = database_engine()
        cls.schema = "security_"+uuid.uuid4().hex
        with cls.admin.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.engine = database_engine()
        @event.listens_for(cls.engine,"connect")
        def namespace(dbapi_conn, _):
            old = dbapi_conn.autocommit
            dbapi_conn.autocommit = True
            with dbapi_conn.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{cls.schema}"')
            dbapi_conn.autocommit = old
        migrate(cls.engine)
        cls.store, cls.access, cls.security = EvidenceStore(cls.engine), OwnerAccess(cls.engine), ConnectionSecurity(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{cls.schema}" CASCADE')
        cls.admin.dispose()
        cls.config.stop()

    def setUp(self):
        os.environ["OWNER_ACCESS_TOKEN"] = OWNER
        with self.engine.begin() as conn:
            conn.execute(delete(m.sessions));conn.execute(delete(m.owner_epoch));conn.execute(delete(m.login_limits))

    def connection(self, write=False):
        source = self.store.snapshot("fixture://security","text/plain",b"synthetic contract")
        connector = self.store.connector("Synthetic")
        artifact = package(source)
        operation = "github_create_issue_v1" if write else "github_get_issue_v1"
        artifact["manifest"]["operations"][0].update(name=operation, classification="write" if write else "read")
        artifact["mappings"][0].update(operation=operation)
        artifact["mappings"][0]["request"]["method"] = "POST" if write else "GET"
        version = self.store.candidate(connector, artifact, [source])
        with self.engine.begin() as conn:
            conn.execute(update(m.versions).where(m.versions.c.id==version).values(status="published",published_by="test-owner",published_at=self.store.now(conn)))
        connection = self.security.create(connector, POLICY, SECRET)
        return connection, version, operation


class FakeDriver:
    def __init__(self, resources=RESOURCES, failure=None):
        self.resolved, self.failure, self.calls = resources, failure, 0

    def resources(self, arguments): return self.resolved
    def execute(self, arguments, credential):
        self.calls += 1
        if self.failure: raise self.failure
        return {"result":"synthetic", "diagnostic":"received "+credential}
    def test(self, credential): return self.execute({}, credential)
