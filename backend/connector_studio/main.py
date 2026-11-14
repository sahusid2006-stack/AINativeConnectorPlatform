"""Infrastructure health and private simulator routing; no connector execution API."""
from contextlib import asynccontextmanager
import os
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .database import database_engine
from .repositories import EvidenceStore

ROLE = os.environ.get("SERVICE_ROLE", "api")
if ROLE not in {"api", "worker", "fixtures"}:
    raise RuntimeError("Unsupported service role")


@asynccontextmanager
async def lifespan(app):
    engine = database_engine()
    app.state.store = EvidenceStore(engine)
    stop = Event()
    thread = None
    if ROLE == "worker":
        def heartbeat():
            while not stop.is_set():
                try:
                    app.state.store.heartbeat("worker_process")
                except SQLAlchemyError:
                    # Includes migration/startup races and database restarts. Never log DSNs.
                    pass
                stop.wait(2)
        thread = Thread(target=heartbeat, daemon=True, name="worker-heartbeat")
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread:
            thread.join(timeout=4)
        engine.dispose()


app = FastAPI(title=f"Connector Studio {ROLE} foundations", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
if ROLE == "fixtures":
    from .github_simulator import router
    app.include_router(router)


@app.get("/health")
def health() -> JSONResponse:
    """Report process availability, explicitly not build/scheduler progress."""
    database_ready = False
    workers = {"worker_process": "degraded", "scheduler": "not_implemented"}
    try:
        with app.state.store.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database_ready = True
        if ROLE in {"api", "worker"}:
            workers.update(app.state.store.worker_health(["worker_process"], stale_seconds=10))
    except SQLAlchemyError:
        # Exceptions can contain connection details; never emit them.
        pass
    status = "unavailable" if not database_ready else (
        "degraded" if ROLE in {"api", "worker"} and workers["worker_process"] != "ready" else "ready")
    return JSONResponse(status_code=200 if status == "ready" else 503, content={
        "service": ROLE, "status": status,
        "stage": "simulator-fixtures" if ROLE == "fixtures" else "infrastructure-only",
        "database": "reachable" if database_ready else "unavailable",
        "workers": workers if ROLE in {"api", "worker"} else {},
        "job_processing": "not_implemented",
        "product_features": "github_simulator_only" if ROLE == "fixtures" else "not_implemented",
    })
