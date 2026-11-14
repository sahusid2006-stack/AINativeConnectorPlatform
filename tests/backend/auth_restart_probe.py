"""Use real provisioned files without exposing values; retain only encrypted probe data."""
import re
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import event, select, text
from connector_studio.database import database_engine, migrate
from connector_studio.repositories import EvidenceStore
from connector_studio.access import OwnerAccess
from connector_studio.policy import ConnectionSecurity, Permit
from connector_studio.provisioning import configured_secret
from tests.security_support import POLICY
import os
import uuid

mode, schema = sys.argv[1:]
assert re.fullmatch(r"security_probe_[0-9a-f]{32}",schema)
admin = database_engine()
if mode == "seed":
    with admin.begin() as conn: conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
engine = database_engine()
@event.listens_for(engine,"connect")
def namespace(dbapi, _):
    dbapi.autocommit = True
    with dbapi.cursor() as cursor: cursor.execute(f'SET search_path TO "{schema}"')
    dbapi.autocommit = False
store, access, security = EvidenceStore(engine), OwnerAccess(engine), ConnectionSecurity(engine)
if mode == "seed":
    migrate(engine)
    connector = store.connector("Security restart probe")
    connection = security.create(connector,POLICY,"synthetic-restart-provider-value")
    session = access.login(configured_secret("OWNER_ACCESS_TOKEN"))
    nonce = os.urandom(12)
    encrypted = nonce+AESGCM(security._key()).encrypt(nonce,session["token"].encode(),b"restart-probe")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE security_probe (connection_id uuid, encrypted_session bytea)")
        conn.execute(text("INSERT INTO security_probe VALUES (:connection,:session)"),{"connection":connection,"session":encrypted})
elif mode == "check":
    with engine.connect() as conn: row = conn.execute(text("SELECT * FROM security_probe")).mappings().one()
    encrypted = row["encrypted_session"]
    token = AESGCM(security._key()).decrypt(encrypted[:12],encrypted[12:],b"restart-probe").decode()
    assert access.authenticate(token)["actor"] == "owner", "Retained owner session must remain valid"
    assert security.resolve(Permit(row["connection_id"],uuid.UUID(int=0),"probe",1,False)) == "synthetic-restart-provider-value", "Retained encrypted secret must decrypt with the same key"
elif mode != "drop":
    raise RuntimeError("Unsupported probe action")
engine.dispose()
if mode == "drop":
    with admin.begin() as conn: conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
admin.dispose()
