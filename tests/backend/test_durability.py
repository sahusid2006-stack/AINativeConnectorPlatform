"""Real PostgreSQL tests in a disposable schema, never a production-table reset."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import uuid
import unittest

from sqlalchemy import event, select, update, insert, delete, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from connector_studio.database import database_engine, migrate
from connector_studio.repositories import EvidenceStore, LostLease
from connector_studio import models as m


def package(source_id):
    return {"manifest": {"operations": [{"name":"github_get_issue_v1", "description":"Get an issue",
        "input_schema":{"type":"object"}, "output_schema":{"type":"object"}, "classification":"read",
        "required_privileges":["issues:read"], "source_evidence":[{"source_id":str(source_id),"excerpt":"GET issue"}],
        "protocol_mapping":"rest", "pagination":None, "timeout_seconds":30,
        "retry_policy":{"max_attempts":3,"safe":True}, "test_references":["get-success"]}]},
        "mappings":[{"operation":"github_get_issue_v1","driver":"rest","request":{"method":"GET"},"output_map":{}}],
        "generated_tests":[], "dependency_lock":{"runtime":"locked"}, "source_snapshot_ids":[str(source_id)]}


class DurabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = database_engine()
        cls.schema = "test_" + uuid.uuid4().hex
        with cls.admin.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.engine = database_engine()
        @event.listens_for(cls.engine, "connect")
        def namespace(dbapi_conn, _):
            old = dbapi_conn.autocommit
            dbapi_conn.autocommit = True
            with dbapi_conn.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{cls.schema}"')
            dbapi_conn.autocommit = old
        migrate(cls.engine)
        cls.store = EvidenceStore(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{cls.schema}" CASCADE')
        cls.admin.dispose()

    def setUp(self):
        # Jobs from prior tests are left as evidence but not runnable.
        with self.engine.begin() as conn:
            conn.execute(update(m.jobs).values(status="blocked", lease_until=None, lease_token=None, lease_owner=None))

    def expire(self, claim):
        with self.engine.begin() as conn:
            conn.execute(update(m.jobs).where(m.jobs.c.id==claim.job_id).values(lease_until=self.store.now(conn)-timedelta(seconds=1)))

    def candidate(self):
        source = self.store.snapshot("fixture://github/v1", "text/markdown", b"GET issue")
        connector = self.store.connector("GitHub")
        return source, connector, self.store.candidate(connector, package(source), [source])

    def test_evidence_and_approvals_survive_new_database_connections(self):
        source, connector, version = self.candidate()
        connection = self.store.connection(connector, {"repo":"demo"}, "secret://test-reference")
        approval = self.store.approval(connection, version, "github_get_issue_v1", "a"*64, "owner")
        report = self.store.report(version, True, {"authorization":"sensitive", "cases":["success"]})
        self.engine.dispose()
        with self.engine.connect() as conn:
            self.assertEqual(conn.scalar(select(m.sources.c.content).where(m.sources.c.id==source)), b"GET issue")
            self.assertEqual(conn.scalar(select(m.approvals.c.actor).where(m.approvals.c.id==approval)), "owner")
            self.assertEqual(conn.scalar(select(m.test_runs.c.report).where(m.test_runs.c.id==report))["authorization"], "[REDACTED]")

    def test_concurrent_claims_do_not_claim_the_same_job(self):
        for _ in range(8): self.store.enqueue("build", {})
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda n:self.store.claim(f"worker-{n}"), range(8)))
        self.assertEqual(len({claim.job_id for claim in claims}), 8)

    def test_build_recovery_preserves_attempts_and_fences_old_worker(self):
        job = self.store.enqueue("build", {"token":"not retained"})
        old = self.store.claim("old")
        self.store.progress(old, {"artifact_diff":"first draft"}, "generating")
        self.expire(old)
        self.assertIn(job, self.store.recover())
        new = self.store.claim("new")
        self.assertEqual(new.attempt_number, 2)
        with self.assertRaises(LostLease): self.store.finish(old, "ready_for_review", {})
        with self.engine.connect() as conn:
            history = conn.execute(select(m.attempts).where(m.attempts.c.job_id==job).order_by(m.attempts.c.attempt_number)).mappings().all()
            self.assertEqual(history[0]["evidence"]["artifact_diff"], "first draft")
            self.assertEqual(history[0]["status"], "interrupted")
            self.assertNotEqual(conn.scalar(select(m.jobs.c.status).where(m.jobs.c.id==job)), "ready_for_review")

    def test_expired_write_intent_becomes_unknown_and_is_not_claimed(self):
        job = self.store.enqueue("write", {})
        claim = self.store.claim("worker")
        self.store.write_intent(claim)
        self.expire(claim)
        self.store.recover()
        self.assertIsNone(self.store.claim("other"))
        with self.engine.connect() as conn:
            self.assertEqual(conn.scalar(select(m.jobs.c.status).where(m.jobs.c.id==job)), "outcome_unknown")

    def test_failed_dispatched_write_is_unknown(self):
        job = self.store.enqueue("write", {})
        claim = self.store.claim("worker")
        self.store.write_intent(claim)
        self.store.finish(claim, "failed", {"error":"transport"})
        with self.engine.connect() as conn:
            self.assertEqual(conn.scalar(select(m.jobs.c.status).where(m.jobs.c.id==job)), "outcome_unknown")

    def test_deadline_blocks_queued_and_interrupted_jobs(self):
        job = self.store.enqueue("build", {})
        with self.engine.begin() as conn:
            conn.execute(update(m.jobs).where(m.jobs.c.id==job).values(deadline=self.store.now(conn)-timedelta(seconds=1)))
        self.store.recover()
        self.assertIsNone(self.store.claim("worker"))
        with self.engine.connect() as conn:
            self.assertEqual(conn.scalar(select(m.jobs.c.status).where(m.jobs.c.id==job)), "blocked")

    def test_renewal_and_expired_lease_fencing(self):
        self.store.enqueue("read", {})
        claim = self.store.claim("worker")
        self.store.renew(claim, 60)
        self.store.progress(claim, {"step":"read"})
        self.expire(claim)
        with self.assertRaises(LostLease): self.store.renew(claim)

    def test_published_artifact_and_operation_evidence_cannot_change(self):
        source, _, version = self.candidate()
        with self.engine.begin() as conn:
            conn.execute(update(m.versions).where(m.versions.c.id==version).values(status="published", published_by="test-only"))
        for statement in [update(m.versions).where(m.versions.c.id==version).values(digest="b"*64),
                          delete(m.versions).where(m.versions.c.id==version),
                          update(m.operations).where(m.operations.c.version_id==version).values(contract={}),
                          delete(m.version_sources).where(m.version_sources.c.version_id==version)]:
            with self.assertRaises(DBAPIError):
                with self.engine.begin() as conn: conn.execute(statement)
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(m.operations.insert().values(version_id=version, name="injected", contract={}))
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn:
                conn.execute(update(m.sources).where(m.sources.c.id==source).values(content=b"changed"))

    def test_pins_prevent_candidate_deletion_and_short_retention_is_rejected(self):
        _, _, version = self.candidate()
        with self.engine.begin() as conn:
            conn.execute(m.version_references.insert().values(owner_kind="workflow",owner_id=uuid.uuid4(),version_id=version))
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn: conn.execute(delete(m.versions).where(m.versions.c.id==version))
        with self.assertRaises(ValueError): self.store.prune_run_metadata(29)

    def test_retention_prunes_only_old_terminal_invocations(self):
        _, _, version = self.candidate()
        with self.engine.begin() as conn:
            now=self.store.now(conn)
            for age,status in [(31,"completed"),(29,"completed"),(31,"outcome_unknown")]:
                conn.execute(m.invocations.insert().values(version_id=version,status=status,trace_id=uuid.uuid4(),metadata={},created_at=now-timedelta(days=age)))
        self.assertEqual(self.store.prune_run_metadata(),1)
        with self.engine.connect() as conn:
            self.assertEqual(conn.scalar(select(func_count()).select_from(m.invocations).where(m.invocations.c.version_id==version)),2)

    def test_audit_is_timestamped_redacted_and_append_only(self):
        job=self.store.enqueue("build", {"password":"must disappear"})
        with self.engine.connect() as conn:
            row=conn.execute(select(m.audit_events).where(m.audit_events.c.resource_id==job)).mappings().one()
            self.assertEqual(row["actor"],"owner"); self.assertIsNotNone(row["created_at"])
            self.assertEqual(conn.scalar(select(m.jobs.c.payload).where(m.jobs.c.id==job))["password"],"[REDACTED]")
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as conn: conn.execute(delete(m.audit_events).where(m.audit_events.c.id==row["id"]))

    def test_dedupe_survives_connection_restart_and_concurrent_delivery(self):
        consumer=uuid.uuid4()
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids=list(pool.map(lambda _:self.store.delivery("github","delivery-1",consumer),range(6)))
        self.assertEqual(len(set(ids)),1)
        self.engine.dispose()
        self.assertEqual(self.store.delivery("github","delivery-1",consumer),ids[0])

    def test_heartbeats_distinguish_missing_and_stale_workers(self):
        self.store.heartbeat("build")
        self.assertEqual(self.store.worker_health(["build","scheduler"]),{"build":"ready","scheduler":"degraded"})
        with self.engine.begin() as conn:
            conn.execute(update(m.heartbeats).where(m.heartbeats.c.worker=="build").values(seen_at=self.store.now(conn)-timedelta(minutes=2)))
        self.assertEqual(self.store.worker_health(["build"]),{"build":"degraded"})

    def test_source_size_limit_and_package_validation(self):
        with self.assertRaises(ValueError): self.store.snapshot("fixture://large","text/plain",b"x"*(2*1024*1024+1))
        source=self.store.snapshot("fixture://source","text/plain",b"small")
        connector=self.store.connector("invalid")
        value=package(source);value["code"]="import os"
        from jsonschema import ValidationError
        with self.assertRaises(ValidationError): self.store.candidate(connector,value,[source])


def func_count():
    from sqlalchemy import func
    return func.count()


if __name__ == "__main__":
    unittest.main()
