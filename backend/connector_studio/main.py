"""Infrastructure health and private simulator routing; no connector execution API."""
from contextlib import asynccontextmanager
import os
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from jsonschema import ValidationError as SchemaError
from pydantic import ValidationError as PolicyError
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
    mapping_health = "not_applicable"
    workers = {"worker_process": "degraded", "scheduler": "not_implemented"}
    try:
        with app.state.store.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database_ready = True
        if ROLE == "api":
            mapping_health = app.state.store.worker_health(["mapping_supervisor"], stale_seconds=15)["mapping_supervisor"]
        if ROLE in {"api", "worker"}:
            workers.update(app.state.store.worker_health(["worker_process"], stale_seconds=10))
    except SQLAlchemyError:
        # Exceptions can contain connection details; never emit them.
        pass
    owner_ready = True
    if ROLE == "api":
        try:
            from .provisioning import configured_secret
            from .policy import ConnectionSecurity
            configured_secret("OWNER_ACCESS_TOKEN")
            ConnectionSecurity(app.state.store.engine)._key()
        except (RuntimeError, AccessError):
            owner_ready = False
    status = "unavailable" if not database_ready else (
        "degraded" if not owner_ready or (ROLE in {"api", "worker"} and workers["worker_process"] != "ready") else "ready")
    return JSONResponse(status_code=200 if status == "ready" else 503, content={
        "service": ROLE, "status": status,
        "stage": "simulator-fixtures" if ROLE == "fixtures" else "deterministic-rest-runtime" if ROLE == "api" else "infrastructure-only",
        "owner_access": "configured" if owner_ready and ROLE == "api" else "unavailable" if ROLE == "api" else "not_applicable",
        "database": "reachable" if database_ready else "unavailable",
        "workers": workers if ROLE in {"api", "worker"} else {},
        "job_processing": "not_implemented",
        "mapping_evaluation": mapping_health,
        "product_features": "github_simulator_only" if ROLE == "fixtures" else "owner_security_source_intake_and_rest_runtime" if ROLE == "api" else "not_implemented",
    })


if ROLE == "api":
    from .access import AccessError, OwnerAccess
    from .control import COOKIE, ORIGIN, router as control_router
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "api"])
    app.include_router(control_router)
    from .source_control import router as source_router
    app.include_router(source_router)
    from .runtime_control import router as runtime_router
    app.include_router(runtime_router)

    @app.middleware("http")
    async def protect_control(request, call_next):
        if request.url.path != "/health":
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") != ORIGIN:
                return JSONResponse({"category":"origin_denied"}, status_code=403)
            try:
                if not (request.url.path == "/auth/login" and request.method == "POST"):
                    request.state.principal = OwnerAccess(app.state.store.engine).authenticate(
                        request.cookies.get(COOKIE), request.headers.get("x-csrf-token"),
                        write=request.method not in {"GET", "HEAD", "OPTIONS"})
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    chunks, size = [], 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > 2*1024*1024:
                            return JSONResponse({"category":"request_too_large"}, status_code=413)
                        chunks.append(chunk)
                    request._body = b"".join(chunks)
                response = await call_next(request)
            except AccessError as error:
                response = JSONResponse({"category":error.category}, status_code=error.status)
            except RuntimeError:
                response = JSONResponse({"category":"secure_configuration_unavailable"}, status_code=503)
            response.headers["Cache-Control"] = "no-store"
            return response
        return await call_next(request)

    @app.exception_handler(AccessError)
    async def access_failure(request, error):
        return JSONResponse({"category":error.category}, status_code=error.status)

    async def invalid_input(request, error):
        # Never echo credential-bearing input or exception diagnostics in validation errors.
        return JSONResponse({"category":"invalid_input"}, status_code=422)

    for error_type in (RequestValidationError, SchemaError, PolicyError):
        app.add_exception_handler(error_type, invalid_input)
