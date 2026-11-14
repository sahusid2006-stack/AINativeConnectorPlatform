"""Database configuration without plaintext DSNs in diagnostics."""
import json
import os
from pathlib import Path

from sqlalchemy import URL, create_engine
from alembic import command
from alembic.config import Config


def database_engine():
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("POSTGRES_PASSWORD must be securely configured")
    url = URL.create("postgresql+psycopg", username="connector_studio", password=password,
                     host=os.environ.get("POSTGRES_HOST", "postgres"), database="connector_studio")
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


def migrate(engine):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    # Serialize migrations across concurrent API/worker starts; never set a plaintext config URL.
    with engine.connect() as conn:
        conn.exec_driver_sql("SELECT pg_advisory_lock(809002)")
        conn.commit()
        try:
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
            conn.commit()
        finally:
            conn.exec_driver_sql("SELECT pg_advisory_unlock(809002)")
            conn.commit()


if __name__ == "__main__":
    import sys
    engine = database_engine()
    if len(sys.argv) != 2 or sys.argv[1] not in {"migrate", "health"}:
        raise SystemExit("Usage: python -m connector_studio.database migrate|health")
    if sys.argv[1] == "migrate":
        migrate(engine)
        print("Database migration complete.")
    else:
        from .repositories import EvidenceStore
        print(json.dumps(EvidenceStore(engine).worker_health(["build", "scheduler"])))
