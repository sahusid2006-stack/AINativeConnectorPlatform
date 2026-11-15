"""Transactional storage interfaces; no management endpoints or provider calls."""
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import uuid

from sqlalchemy import select, update, delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from . import models as m

from .redaction import redacted

@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    token: uuid.UUID
    worker: str
    attempt_number: int


class LostLease(RuntimeError):
    pass


class EvidenceStore:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def now(conn):
        return conn.scalar(select(func.clock_timestamp()))

    def audit(self, conn, actor, action, resource_id=None, details=None):
        conn.execute(m.audit_events.insert().values(actor=actor, action=action, resource_id=resource_id,
                                                   details=redacted(details or {})))

    def snapshot(self, origin, mime_type, content, actor="owner"):
        if len(content) > 2 * 1024 * 1024:
            raise ValueError("Source exceeds 2 MB limit")
        with self.engine.begin() as conn:
            record_id = conn.scalar(m.sources.insert().values(origin=origin, mime_type=mime_type,
                content=content, digest=hashlib.sha256(content).hexdigest()).returning(m.sources.c.id))
            self.audit(conn, actor, "source.retained", record_id)
            return record_id

    def connector(self, name, protocol="rest", actor="owner"):
        with self.engine.begin() as conn:
            record_id = conn.scalar(m.connectors.insert().values(name=name, protocol=protocol).returning(m.connectors.c.id))
            self.audit(conn, actor, "connector.created", record_id)
            return record_id

    def candidate(self, connector_id, package, source_ids, public_major=1, revision=1, actor="worker"):
        from .contracts import validate_package
        validate_package(package, source_ids)
        artifact = json.dumps(package, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(artifact) > 2 * 1024 * 1024:
            raise ValueError("Artifact exceeds 2 MB limit")
        with self.engine.begin() as conn:
            record_id = conn.scalar(m.versions.insert().values(connector_id=connector_id, public_major=public_major,
                revision=revision, status="candidate", artifact=artifact, digest=hashlib.sha256(artifact).hexdigest(),
                manifest=package["manifest"]).returning(m.versions.c.id))
            for source_id in set(source_ids):
                conn.execute(m.version_sources.insert().values(version_id=record_id, source_id=source_id))
            for operation in package["manifest"]["operations"]:
                conn.execute(m.operations.insert().values(version_id=record_id, name=operation["name"], contract=operation))
            self.audit(conn, actor, "candidate.created", record_id)
            return record_id

    def report(self, version_id, passed, report, mode="simulator", actor="acceptance-runner"):
        with self.engine.begin() as conn:
            record_id = conn.scalar(m.test_runs.insert().values(version_id=version_id, passed=passed,
                verification_mode=mode, report=redacted(report)).returning(m.test_runs.c.id))
            self.audit(conn, actor, "test.recorded", record_id)
            return record_id

    def enqueue(self, kind, payload, connector_id=None, budget_seconds=600, actor="owner"):
        if not 1 <= budget_seconds <= 3600:
            raise ValueError("Job budget must be 1..3600 seconds")
        with self.engine.begin() as conn:
            record_id = conn.scalar(m.jobs.insert().values(kind=kind, payload=redacted(payload), connector_id=connector_id,
                deadline=self.now(conn) + timedelta(seconds=budget_seconds)).returning(m.jobs.c.id))
            self.audit(conn, actor, "job.queued", record_id)
            return record_id

    def claim(self, worker, lease_seconds=30):
        if not worker or not 1 <= lease_seconds <= 300:
            raise ValueError("A worker and 1..300 second lease are required")
        with self.engine.begin() as conn:
            now = self.now(conn)
            row = conn.execute(select(m.jobs).where(m.jobs.c.status == "queued", m.jobs.c.deadline > now)
                .order_by(m.jobs.c.created_at, m.jobs.c.id).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if row is None:
                return None
            token = uuid.uuid4()
            attempt = row["attempt_count"] + 1
            conn.execute(update(m.jobs).where(m.jobs.c.id == row["id"]).values(status="running", lease_owner=worker,
                lease_token=token, lease_until=min(now + timedelta(seconds=lease_seconds), row["deadline"]), attempt_count=attempt))
            conn.execute(m.attempts.insert().values(job_id=row["id"], attempt_number=attempt, lease_token=token, status="running"))
            self.audit(conn, worker, "job.claimed", row["id"], {"attempt": attempt})
            return Claim(row["id"], token, worker, attempt)

    def _leased(self, conn, claim):
        row = conn.execute(select(m.jobs).where(m.jobs.c.id == claim.job_id).with_for_update()).mappings().first()
        now = self.now(conn)
        if not row or row["lease_token"] != claim.token or row["lease_owner"] != claim.worker or not row["lease_until"] or row["lease_until"] <= now:
            raise LostLease("Job lease expired or replaced")
        return row

    def progress(self, claim, evidence, stage="running"):
        if stage not in {"running", "reading_sources", "generating", "testing"}:
            raise ValueError("Unsupported active stage")
        with self.engine.begin() as conn:
            self._leased(conn, claim)
            conn.execute(update(m.jobs).where(m.jobs.c.id == claim.job_id).values(status=stage))
            conn.execute(update(m.attempts).where(m.attempts.c.lease_token == claim.token).values(evidence=redacted(evidence)))

    def renew(self, claim, lease_seconds=30):
        if not 1 <= lease_seconds <= 300:
            raise ValueError("Lease must be 1..300 seconds")
        with self.engine.begin() as conn:
            row = self._leased(conn, claim)
            conn.execute(update(m.jobs).where(m.jobs.c.id == claim.job_id).values(
                lease_until=min(self.now(conn) + timedelta(seconds=lease_seconds), row["deadline"])))

    def write_intent(self, claim):
        with self.engine.begin() as conn:
            row = self._leased(conn, claim)
            if row["kind"] != "write":
                raise ValueError("Only write jobs can record dispatch intent")
            conn.execute(update(m.jobs).where(m.jobs.c.id == claim.job_id).values(write_intent=True))
            self.audit(conn, claim.worker, "write.intent", claim.job_id)

    def finish(self, claim, status, evidence):
        if status not in {"completed", "ready_for_review", "failed", "blocked", "outcome_unknown"}:
            raise ValueError("Unsupported terminal status")
        with self.engine.begin() as conn:
            row = self._leased(conn, claim)
            if row["write_intent"] and status == "failed":
                status = "outcome_unknown"
            conn.execute(update(m.jobs).where(m.jobs.c.id == claim.job_id).values(status=status, lease_owner=None,
                lease_token=None, lease_until=None))
            conn.execute(update(m.attempts).where(m.attempts.c.lease_token == claim.token).values(
                status=status, evidence=redacted(evidence), finished_at=self.now(conn)))
            self.audit(conn, claim.worker, "job.finished", claim.job_id, {"status": status})

    def recover(self):
        recovered = []
        with self.engine.begin() as conn:
            now = self.now(conn)
            rows = conn.execute(select(m.jobs).where(m.jobs.c.lease_until <= now)
                .with_for_update(skip_locked=True)).mappings().all()
            for row in rows:
                status = "outcome_unknown" if row["write_intent"] else "blocked" if row["deadline"] <= now else "queued"
                conn.execute(update(m.jobs).where(m.jobs.c.id == row["id"]).values(status=status,
                    lease_owner=None, lease_token=None, lease_until=None))
                conn.execute(update(m.attempts).where(m.attempts.c.lease_token == row["lease_token"]).values(
                    status="outcome_unknown" if row["write_intent"] else "interrupted", finished_at=now))
                if status == "outcome_unknown" and row["payload"].get("invocation_id"):
                    try: invocation_id=uuid.UUID(row["payload"]["invocation_id"])
                    except (ValueError,TypeError,AttributeError): invocation_id=None
                    if invocation_id:
                        invocation=conn.execute(select(m.invocations).where(m.invocations.c.id==invocation_id,m.invocations.c.status=="running").with_for_update()).mappings().first()
                        if invocation:
                            metadata={**invocation["metadata"],"result":{"trace_id":str(invocation_id),"version_id":str(invocation["version_id"]),"status":"outcome_unknown","attempts":1,"error":{"category":"outcome_unknown","reconciliation_required":True}}}
                            conn.execute(update(m.invocations).where(m.invocations.c.id==invocation_id).values(status="outcome_unknown",metadata=metadata))
                self.audit(conn, "recovery", "job.recovered", row["id"], {"status": status})
                recovered.append(row["id"])
            # Expired queued jobs must terminate, not remain apparently runnable forever.
            conn.execute(update(m.jobs).where(m.jobs.c.status == "queued", m.jobs.c.deadline <= now).values(status="blocked"))
        return recovered

    def heartbeat(self, worker):
        with self.engine.begin() as conn:
            conn.execute(pg_insert(m.heartbeats).values(worker=worker, seen_at=self.now(conn)).on_conflict_do_update(
                index_elements=[m.heartbeats.c.worker], set_={"seen_at": self.now(conn)}))

    def worker_health(self, workers, stale_seconds=60):
        with self.engine.begin() as conn:
            cutoff = self.now(conn) - timedelta(seconds=stale_seconds)
            seen = dict(conn.execute(select(m.heartbeats.c.worker, m.heartbeats.c.seen_at)).all())
            return {worker: "ready" if worker in seen and seen[worker] >= cutoff else "degraded" for worker in workers}

    def prune_run_metadata(self, retention_days=30):
        if retention_days < 30:
            raise ValueError("Run metadata must be retained at least 30 days")
        with self.engine.begin() as conn:
            result = conn.execute(delete(m.invocations).where(
                m.invocations.c.created_at < self.now(conn) - timedelta(days=retention_days),
                m.invocations.c.status.in_(["completed", "failed"])))
            # Never delete versions, approvals, sources, dedupe records, attempts, or unknown writes here.
            return result.rowcount

    def connection(self, connector_id, resource_policy, credential_reference=None, mode="simulator", actor="owner"):
        if credential_reference and not credential_reference.startswith("secret://"):
            raise ValueError("Only a secret reference, never a credential value, may be retained")
        with self.engine.begin() as conn:
            record_id = conn.scalar(m.connections.insert().values(connector_id=connector_id, resource_policy=redacted(resource_policy),
                credential_reference=credential_reference, mode=mode).returning(m.connections.c.id))
            self.audit(conn, actor, "connection.created", record_id)
            return record_id

    def approval(self, connection_id, version_id, operation, arguments_digest, actor, expires_seconds=600):
        if not 1 <= expires_seconds <= 3600:
            raise ValueError("Approval lifetime must be 1..3600 seconds")
        with self.engine.begin() as conn:
            record_id = conn.scalar(m.approvals.insert().values(connection_id=connection_id, version_id=version_id,
                operation=operation, arguments_digest=arguments_digest, actor=actor,
                expires_at=self.now(conn)+timedelta(seconds=expires_seconds)).returning(m.approvals.c.id))
            self.audit(conn, actor, "approval.recorded", record_id)
            return record_id

    def delivery(self, provider, delivery_id, consumer_id):
        with self.engine.begin() as conn:
            record_id = conn.scalar(pg_insert(m.deliveries).values(provider=provider, delivery_id=delivery_id,
                consumer_id=consumer_id, status="received").on_conflict_do_nothing().returning(m.deliveries.c.id))
            if record_id:
                return record_id
            return conn.scalar(select(m.deliveries.c.id).where(m.deliveries.c.provider==provider,
                m.deliveries.c.delivery_id==delivery_id, m.deliveries.c.consumer_id==consumer_id))
