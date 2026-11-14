"""Cross-process evidence restart probe, isolated in its own disposable schema."""
import json
import re
import sys
import uuid
from sqlalchemy import event, select, text
from connector_studio.database import database_engine, migrate
from connector_studio.repositories import EvidenceStore
from connector_studio import models as m
from tests.test_durability import package

mode, schema = sys.argv[1:]
if not re.fullmatch(r"probe_[a-f0-9]{32}", schema):
    raise SystemExit("Invalid probe schema")
admin=database_engine()
if mode == "drop":
    with admin.begin() as conn: conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    raise SystemExit(0)
if mode == "seed":
    with admin.begin() as conn: conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
engine=database_engine()
@event.listens_for(engine,"connect")
def namespace(dbapi_conn,_):
    old=dbapi_conn.autocommit;dbapi_conn.autocommit=True
    with dbapi_conn.cursor() as cursor: cursor.execute(f'SET search_path TO "{schema}"')
    dbapi_conn.autocommit=old
store=EvidenceStore(engine)
if mode == "seed":
    migrate(engine)
    source=store.snapshot("fixture://restart-probe","text/plain",b"retained documentation")
    connector=store.connector("Restart probe")
    version=store.candidate(connector,package(source),[source])
    connection=store.connection(connector,{"repository":"synthetic"})
    approval=store.approval(connection,version,"github_get_issue_v1","a"*64,"probe-owner")
    store.report(version,True,{"probe":"fixed"})
    job=store.enqueue("build",{})
    claim=store.claim("probe-worker",300)
    store.progress(claim,{"artifact_diff":"retained draft"},"generating")
    consumer=uuid.uuid4();delivery=store.delivery("github","restart-probe",consumer)
    values={k:str(v) for k,v in locals().copy().items() if k in {"source","version","approval","job","consumer","delivery"}}
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE probe_ids (ids jsonb)")
        conn.execute(text("INSERT INTO probe_ids VALUES (CAST(:ids AS jsonb))"),{"ids":json.dumps(values)})
elif mode == "check":
    with engine.connect() as conn:
        ids={k:uuid.UUID(v) for k,v in conn.scalar(text("SELECT ids FROM probe_ids")).items()}
        assert conn.scalar(select(m.sources.c.content).where(m.sources.c.id==ids["source"]))==b"retained documentation"
        assert conn.scalar(select(m.versions.c.artifact).where(m.versions.c.id==ids["version"]))
        assert conn.scalar(select(m.approvals.c.actor).where(m.approvals.c.id==ids["approval"]))=="probe-owner"
        assert conn.scalar(select(m.jobs.c.status).where(m.jobs.c.id==ids["job"]))=="generating"
        assert conn.scalar(select(m.attempts.c.evidence).where(m.attempts.c.job_id==ids["job"]))["artifact_diff"]=="retained draft"
        assert conn.scalar(select(m.test_runs.c.passed).where(m.test_runs.c.version_id==ids["version"])) is True
    assert store.delivery("github","restart-probe",ids["consumer"])==ids["delivery"]
    print("Sources, artifacts, approvals, attempts, test reports, and dedupe records survived restart.")
else:
    raise SystemExit("Unknown probe mode")
