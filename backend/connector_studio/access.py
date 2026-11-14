"""Opaque persisted owner sessions and protected API boundary."""
from datetime import timedelta
import hashlib
import hmac
import secrets

from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import models as m
from .provisioning import configured_secret
from .repositories import EvidenceStore
from .redaction import remember


class AccessError(Exception):
    def __init__(self, category, status=403):
        self.category, self.status = category, status
        super().__init__(category)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class OwnerAccess:
    def __init__(self, engine):
        self.engine, self.store = engine, EvidenceStore(engine)

    def epoch(self, conn):
        owner = configured_secret("OWNER_ACCESS_TOKEN")
        fingerprint = digest(owner)
        conn.execute(pg_insert(m.owner_epoch).values(id=1, fingerprint=fingerprint).on_conflict_do_nothing())
        row = conn.execute(select(m.owner_epoch).where(m.owner_epoch.c.id == 1).with_for_update()).mappings().one()
        if not hmac.compare_digest(row["fingerprint"], fingerprint):
            conn.execute(delete(m.sessions))
            conn.execute(update(m.owner_epoch).where(m.owner_epoch.c.id == 1).values(fingerprint=fingerprint))
            self.store.audit(conn, "secure-configuration", "owner.credential_rotated")
        return owner, fingerprint

    def login(self, supplied):
        error = None
        session = None
        with self.engine.begin() as conn:
            owner, fingerprint = self.epoch(conn)
            now = self.store.now(conn)
            conn.execute(pg_insert(m.login_limits).values(id=1, attempts=0, window_start=now).on_conflict_do_nothing())
            limit = conn.execute(select(m.login_limits).where(m.login_limits.c.id == 1).with_for_update()).mappings().one()
            attempts = limit["attempts"] if limit["window_start"] > now - timedelta(minutes=1) else 0
            if attempts >= 5:
                error = AccessError("login_rate_limited", 429)
            elif not hmac.compare_digest(supplied.encode(), owner.encode()):
                conn.execute(update(m.login_limits).where(m.login_limits.c.id == 1).values(
                    attempts=attempts+1, window_start=limit["window_start"] if attempts else now))
                self.store.audit(conn, "unauthenticated", "owner.login_failed")
                error = AccessError("authentication_failed", 401)
            else:
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                conn.execute(m.sessions.insert().values(token_hash=digest(token), fingerprint=fingerprint,
                    csrf_token=csrf, expires_at=now+timedelta(hours=8)))
                conn.execute(update(m.login_limits).where(m.login_limits.c.id == 1).values(attempts=0))
                self.store.audit(conn, "owner", "owner.login")
                session = {"token":token, "csrf_token":csrf, "actor":"owner"}
        if error:
            raise error
        remember(session["token"])
        remember(session["csrf_token"])
        return session

    def authenticate(self, token, csrf=None, write=False):
        result = None
        with self.engine.begin() as conn:
            _, fingerprint = self.epoch(conn)
            row = conn.execute(select(m.sessions).where(m.sessions.c.token_hash == digest(token or ""),
                m.sessions.c.expires_at > self.store.now(conn), m.sessions.c.fingerprint == fingerprint)).mappings().first()
            if row and (not write or hmac.compare_digest(csrf or "", row["csrf_token"])):
                result = {"actor":"owner", "csrf_token":row["csrf_token"]}
        if not result:
            raise AccessError("unauthenticated" if not row else "csrf_failed", 401 if not row else 403)
        remember(token)
        remember(result["csrf_token"])
        return result

    def logout(self, token):
        with self.engine.begin() as conn:
            conn.execute(delete(m.sessions).where(m.sessions.c.token_hash == digest(token)))
            self.store.audit(conn, "owner", "owner.logout")
