"""Trusted resource decisions, encrypted secrets, and exact single-use write approvals."""
import base64
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jsonschema import ValidationError
from .contracts import offline_validator
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from . import models as m
from .access import AccessError
from .provisioning import configured_secret
from .redaction import redacted, remember
from .repositories import Claim, EvidenceStore, LostLease


class ResourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    destinations: list[str] = Field(min_length=1, max_length=10)
    repositories: list[str] = Field(default_factory=list, max_length=20)
    channels: list[str] = Field(default_factory=list, max_length=20)
    tables: dict[str, list[str]] = Field(default_factory=dict)

    def checked(self):
        from urllib.parse import urlsplit
        for destination in self.destinations:
            url = urlsplit(destination)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
                raise AccessError("invalid_resource_policy", 422)
            if destination != f'{url.scheme}://{url.netloc}':
                raise AccessError("invalid_resource_policy", 422)
        values = self.destinations + self.repositories + self.channels + list(self.tables)
        values += [column for columns in self.tables.values() for column in columns]
        if any(not value or "*" in value or len(value) > 512 for value in values):
            raise AccessError("invalid_resource_policy", 422)
        return self


class ResourceContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    destination: str
    repository: str | None = None
    channel: str | None = None
    table: str | None = None
    columns: list[str] = Field(default_factory=list)


def check_resources(policy, resources):
    policy = ResourcePolicy.model_validate(policy).checked()
    context = ResourceContext.model_validate(resources)
    denied = context.destination not in policy.destinations
    denied |= bool(policy.repositories) and context.repository not in policy.repositories
    denied |= context.repository is not None and context.repository not in policy.repositories
    denied |= bool(policy.channels) and context.channel not in policy.channels
    denied |= context.channel is not None and context.channel not in policy.channels
    if policy.tables or context.table is not None:
        denied |= context.table not in policy.tables or not context.columns
        denied |= not set(context.columns).issubset(policy.tables.get(context.table, []))
    if denied:
        raise AccessError("resource_denied")


def arguments_digest(arguments):
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 131072:
        raise AccessError("arguments_too_large", 422)
    return hashlib.sha256(encoded).hexdigest()


class DriverFailure(Exception):
    def __init__(self, category):
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class Permit:
    connection_id: uuid.UUID
    version_id: uuid.UUID
    operation: str
    policy_revision: int
    write: bool


class ConnectionSecurity:
    def __init__(self, engine):
        self.engine, self.store = engine, EvidenceStore(engine)

    def _key(self):
        try:
            key = base64.b64decode(configured_secret("SECRET_ENCRYPTION_KEY"), altchars=b"-_", validate=True)
            if len(key) != 32:
                raise ValueError()
            return key
        except ValueError:
            raise AccessError("secret_configuration_unavailable", 503) from None

    def _encrypt(self, value, connection_id):
        remember(value)
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key()).encrypt(nonce, value.encode(), str(connection_id).encode())

    def _connection(self, conn, connection_id):
        row = conn.execute(select(m.connections).where(m.connections.c.id == connection_id).with_for_update()).mappings().first()
        if not row:
            raise AccessError("connection_not_found", 404)
        if row["mode"] != "simulator":
            raise AccessError("live_verification_pending")
        return row

    def create(self, connector_id, policy, credential=None, actor="owner"):
        policy = ResourcePolicy.model_validate(policy).checked().model_dump()
        with self.engine.begin() as conn:
            if not conn.scalar(select(m.connectors.c.id).where(m.connectors.c.id == connector_id)):
                raise AccessError("connector_not_found", 404)
            connection_id = uuid.uuid4()
            secret_id = uuid.uuid4() if credential else None
            conn.execute(m.connections.insert().values(id=connection_id, connector_id=connector_id,
                mode="simulator", resource_policy=policy, credential_reference=f"secret://{secret_id}" if secret_id else None))
            if secret_id:
                conn.execute(m.connection_secrets.insert().values(id=secret_id, connection_id=connection_id,
                    ciphertext=self._encrypt(credential, connection_id)))
            self.store.audit(conn, actor, "connection.created", connection_id)
        return connection_id

    def resolve(self, permit):
        with self.engine.begin() as conn:
            connection = self._connection(conn, permit.connection_id)
            if connection["policy_revision"] != permit.policy_revision:
                raise AccessError("connection_changed")
            row = conn.execute(select(m.connection_secrets).where(m.connection_secrets.c.connection_id == permit.connection_id)).mappings().first()
            if not row or row["revoked_at"]:
                raise AccessError("reconnection_required")
            try:
                value = AESGCM(self._key()).decrypt(row["ciphertext"][:12], row["ciphertext"][12:], str(permit.connection_id).encode()).decode()
            except (InvalidTag, UnicodeError, ValueError):
                raise AccessError("reconnection_required") from None
            remember(value)
            return value

    def change(self, connection_id, policy=None, credential=None, revoke=False, actor="owner"):
        with self.engine.begin() as conn:
            current = self._connection(conn, connection_id)
            values = {"policy_revision":current["policy_revision"]+1}
            if policy is not None:
                values["resource_policy"] = ResourcePolicy.model_validate(policy).checked().model_dump()
            if credential is not None:
                secret = conn.scalar(select(m.connection_secrets.c.id).where(m.connection_secrets.c.connection_id == connection_id))
                ciphertext = self._encrypt(credential, connection_id)
                if secret:
                    conn.execute(update(m.connection_secrets).where(m.connection_secrets.c.id == secret).values(ciphertext=ciphertext, revoked_at=None))
                else:
                    secret = uuid.uuid4()
                    conn.execute(m.connection_secrets.insert().values(id=secret, connection_id=connection_id, ciphertext=ciphertext))
                values["credential_reference"] = f"secret://{secret}"
            if revoke:
                conn.execute(update(m.connection_secrets).where(m.connection_secrets.c.connection_id == connection_id).values(revoked_at=self.store.now(conn)))
            conn.execute(update(m.connections).where(m.connections.c.id == connection_id).values(**values))
            conn.execute(update(m.approvals).where(m.approvals.c.connection_id == connection_id, m.approvals.c.revoked_at.is_(None)).values(revoked_at=self.store.now(conn)))
            self.store.audit(conn, actor, "connection.revoked" if revoke else "connection.changed", connection_id)

    def _operation(self, conn, connection, version_id, operation, arguments):
        version = conn.execute(select(m.versions).where(m.versions.c.id == version_id)).mappings().first()
        contract = conn.scalar(select(m.operations.c.contract).where(m.operations.c.version_id == version_id, m.operations.c.name == operation))
        if not version or version["connector_id"] != connection["connector_id"] or version["status"] != "published" or not contract:
            raise AccessError("version_or_operation_denied")
        arguments_digest(arguments)
        try:
            offline_validator(contract["input_schema"]).validate(arguments)
        except ValidationError:
            raise
        except Exception:
            raise AccessError("invalid_contract", 422) from None
        return contract

    def approve(self, connection_id, version_id, operation, arguments, expires_seconds=600, actor="owner"):
        if not 1 <= expires_seconds <= 600:
            raise AccessError("invalid_approval_expiry", 422)
        with self.engine.begin() as conn:
            connection = self._connection(conn, connection_id)
            contract = self._operation(conn, connection, version_id, operation, arguments)
            if contract["classification"] != "write":
                raise AccessError("approval_only_for_writes", 422)
            approval_id = conn.scalar(m.approvals.insert().values(connection_id=connection_id, version_id=version_id,
                operation=operation, arguments_digest=arguments_digest(arguments), actor=actor,
                expires_at=self.store.now(conn)+timedelta(seconds=expires_seconds), policy_revision=connection["policy_revision"]).returning(m.approvals.c.id))
            self.store.audit(conn, actor, "approval.recorded", approval_id)
            return approval_id

    def revoke_approval(self, approval_id, actor="owner"):
        with self.engine.begin() as conn:
            changed = conn.execute(update(m.approvals).where(m.approvals.c.id == approval_id,
                m.approvals.c.revoked_at.is_(None)).values(revoked_at=self.store.now(conn)))
            if not changed.rowcount:
                raise AccessError("approval_not_found_or_revoked", 404)
            self.store.audit(conn, actor, "approval.revoked", approval_id)

    def permit(self, connection_id, version_id, operation, arguments, resources, approval_id=None):
        with self.engine.begin() as conn:
            connection = self._connection(conn, connection_id)
            check_resources(connection["resource_policy"], resources)
            contract = self._operation(conn, connection, version_id, operation, arguments)
            write = contract["classification"] == "write"
            if write:
                consumed = conn.execute(update(m.approvals).where(m.approvals.c.id == approval_id,
                    m.approvals.c.connection_id == connection_id, m.approvals.c.version_id == version_id,
                    m.approvals.c.operation == operation, m.approvals.c.arguments_digest == arguments_digest(arguments),
                    m.approvals.c.policy_revision == connection["policy_revision"],
                    m.approvals.c.expires_at > self.store.now(conn), m.approvals.c.consumed_at.is_(None),
                    m.approvals.c.revoked_at.is_(None)).values(consumed_at=self.store.now(conn)))
                if not consumed.rowcount:
                    raise AccessError("approval_required")
                self.store.audit(conn, "owner", "approval.consumed", approval_id)
            return Permit(connection_id, version_id, operation, connection["policy_revision"], write)

    def call(self, driver, connection_id, version_id, operation, arguments, approval_id=None):
        # resources() is trusted driver code, not a caller's claim about the destination.
        permit = self.permit(connection_id, version_id, operation, arguments, driver.resources(arguments), approval_id)
        credential = self.resolve(permit)
        claim = None
        if permit.write:
            with self.engine.begin() as conn:
                now, token, job_id = self.store.now(conn), uuid.uuid4(), uuid.uuid4()
                conn.execute(m.jobs.insert().values(id=job_id, kind="write", status="running", payload={"operation":operation},
                    lease_owner="trusted-runtime", lease_token=token, lease_until=now+timedelta(seconds=30),
                    deadline=now+timedelta(seconds=30), attempt_count=1, write_intent=True))
                conn.execute(m.attempts.insert().values(job_id=job_id, attempt_number=1, lease_token=token, status="running"))
                self.store.audit(conn, "owner", "write.intent", job_id)
                claim = Claim(job_id, token, "trusted-runtime", 1)
        try:
            result = redacted(driver.execute(arguments, credential))
            if claim:
                self.store.finish(claim, "completed", {"status":"completed"})
            return result
        except Exception:
            if claim:
                try:
                    self.store.finish(claim, "outcome_unknown", {"category":"outcome_unknown"})
                except LostLease:
                    self.store.recover()
                raise AccessError("outcome_unknown", 409) from None
            raise AccessError("upstream_error", 502) from None

    def test(self, driver, connection_id):
        actions = {"authentication":"Reconnect with valid credentials", "permission":"Grant the required privileges",
            "unreachable":"Check the service and destination", "unsupported_auth":"Use a supported authentication scheme"}
        try:
            with self.engine.begin() as conn:
                connection = self._connection(conn, connection_id)
                check_resources(connection["resource_policy"], driver.resources({}))
                permit = Permit(connection_id, uuid.UUID(int=0), "connection-test", connection["policy_revision"], False)
            credential = self.resolve(permit)
            result = redacted(driver.test(credential))
            return {"category":"success", "mode":"simulator", "rewrite_adapter":False, "evidence":result}
        except DriverFailure as error:
            category = error.category if error.category in actions else "upstream_error"
            return {"category":category, "next_action":actions.get(category,"Inspect the upstream service"), "mode":"simulator", "rewrite_adapter":False}
