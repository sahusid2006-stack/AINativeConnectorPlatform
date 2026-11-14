"""Health-only process scaffolds. No product or provider invocation routes."""

import os

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

ROLE = os.environ.get("SERVICE_ROLE", "api")
if ROLE not in {"api", "worker", "fixtures"}:
    raise RuntimeError("Unsupported service role")

app = FastAPI(title=f"Connector Studio {ROLE} bootstrap", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> JSONResponse:
    """Check infrastructure without disclosing connection details or secrets."""
    database_ready = False
    password = os.environ.get("POSTGRES_PASSWORD", "")
    if password:
        try:
            with psycopg.connect(
                host=os.environ.get("POSTGRES_HOST", "postgres"),
                dbname="connector_studio",
                user="connector_studio",
                password=password,
                connect_timeout=3,
            ) as conn:
                conn.execute("SELECT 1")
            database_ready = True
        except psycopg.Error:
            # Connection exceptions can contain sensitive DSNs. Never emit them.
            pass
    return JSONResponse(
        status_code=200 if database_ready else 503,
        content={
            "service": ROLE,
            "status": "ready" if database_ready else "unavailable",
            "stage": "infrastructure-only",
            "database": "reachable" if database_ready else "unavailable",
            "product_features": "not_implemented",
        },
    )
