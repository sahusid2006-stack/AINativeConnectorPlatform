"""Canonical durable storage. Workflow/drift orchestration is deliberately absent."""
import uuid
from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Boolean, LargeBinary, DateTime,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB

metadata = MetaData()


def identity():
    return Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def created():
    return Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()"))


sources = Table("source_snapshots", metadata, identity(), created(),
    Column("origin", String, nullable=False), Column("mime_type", String, nullable=False),
    Column("content", LargeBinary, nullable=False), Column("digest", String(64), nullable=False),
    CheckConstraint("octet_length(content) <= 2097152", name="source_size_limit"))
connectors = Table("connectors", metadata, identity(), created(),
    Column("name", String, nullable=False), Column("protocol", String, nullable=False),
    Column("status", String, nullable=False, server_default="draft"))
versions = Table("connector_versions", metadata, identity(), created(),
    Column("connector_id", ForeignKey("connectors.id"), nullable=False),
    Column("public_major", Integer, nullable=False), Column("revision", Integer, nullable=False),
    Column("status", String, nullable=False, server_default="candidate"),
    Column("artifact", LargeBinary, nullable=False), Column("digest", String(64), nullable=False),
    Column("manifest", JSONB, nullable=False), Column("published_by", String),
    Column("published_at", DateTime(timezone=True)),
    CheckConstraint("public_major > 0 AND revision > 0", name="positive_version"),
    CheckConstraint("status IN ('candidate', 'published')", name="version_status"),
    CheckConstraint("octet_length(artifact) <= 2097152", name="artifact_size_limit"),
    UniqueConstraint("connector_id", "public_major", "revision"))
version_sources = Table("version_sources", metadata,
    Column("version_id", ForeignKey("connector_versions.id"), primary_key=True),
    Column("source_id", ForeignKey("source_snapshots.id"), primary_key=True))
operations = Table("operations", metadata,
    Column("version_id", ForeignKey("connector_versions.id"), primary_key=True),
    Column("name", String, primary_key=True), Column("contract", JSONB, nullable=False))
connections = Table("connections", metadata, identity(), created(),
    Column("connector_id", ForeignKey("connectors.id"), nullable=False),
    Column("credential_reference", String), Column("resource_policy", JSONB, nullable=False),
    Column("mode", String, nullable=False), Column("policy_revision", Integer, nullable=False, server_default="1"),
    CheckConstraint("mode IN ('simulator', 'live')", name="connection_mode"))
approvals = Table("approvals", metadata, identity(), created(),
    Column("connection_id", ForeignKey("connections.id"), nullable=False),
    Column("version_id", ForeignKey("connector_versions.id"), nullable=False),
    Column("operation", String, nullable=False), Column("arguments_digest", String(64), nullable=False),
    Column("actor", String, nullable=False), Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)), Column("revoked_at", DateTime(timezone=True)))
jobs = Table("build_jobs", metadata, identity(), created(),
    Column("connector_id", ForeignKey("connectors.id")), Column("kind", String, nullable=False),
    Column("status", String, nullable=False, server_default="queued"),
    Column("payload", JSONB, nullable=False), Column("deadline", DateTime(timezone=True), nullable=False),
    Column("lease_owner", String), Column("lease_token", UUID(as_uuid=True)),
    Column("lease_until", DateTime(timezone=True)),
    Column("write_intent", Boolean, nullable=False, server_default=text("false")),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    CheckConstraint("kind IN ('build', 'read', 'write')", name="job_kind"),
    CheckConstraint("status IN ('queued','reading_sources','generating','testing','running','ready_for_review','failed','blocked','outcome_unknown','completed')", name="job_status"))
Index("job_claim_index", jobs.c.status, jobs.c.created_at)
attempts = Table("build_attempts", metadata, identity(), created(),
    Column("job_id", ForeignKey("build_jobs.id"), nullable=False),
    Column("attempt_number", Integer, nullable=False), Column("lease_token", UUID(as_uuid=True), nullable=False),
    Column("status", String, nullable=False), Column("evidence", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("finished_at", DateTime(timezone=True)), UniqueConstraint("job_id", "attempt_number"))
test_runs = Table("test_runs", metadata, identity(), created(),
    Column("version_id", ForeignKey("connector_versions.id"), nullable=False),
    Column("verification_mode", String, nullable=False), Column("passed", Boolean, nullable=False),
    Column("report", JSONB, nullable=False),
    CheckConstraint("verification_mode IN ('simulator','live','replay')", name="verification_mode"))
audit_events = Table("audit_events", metadata, identity(), created(),
    Column("actor", String, nullable=False), Column("action", String, nullable=False),
    Column("resource_id", UUID(as_uuid=True)), Column("details", JSONB, nullable=False))
heartbeats = Table("worker_heartbeats", metadata,
    Column("worker", String, primary_key=True), Column("seen_at", DateTime(timezone=True), nullable=False))
invocations = Table("invocation_runs", metadata, identity(), created(),
    Column("version_id", ForeignKey("connector_versions.id"), nullable=False),
    Column("status", String, nullable=False), Column("trace_id", UUID(as_uuid=True), nullable=False),
    Column("metadata", JSONB, nullable=False))
version_references = Table("version_references", metadata,
    Column("owner_kind", String, primary_key=True), Column("owner_id", UUID(as_uuid=True), primary_key=True),
    Column("version_id", ForeignKey("connector_versions.id"), primary_key=True))
deliveries = Table("delivery_deduplication", metadata, identity(), created(),
    Column("provider", String, nullable=False), Column("delivery_id", String, nullable=False),
    Column("consumer_id", UUID(as_uuid=True), nullable=False), Column("status", String, nullable=False),
    UniqueConstraint("provider", "delivery_id", "consumer_id"))
