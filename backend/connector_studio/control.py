"""Owner-only backend control routes. Actual provider runtimes integrate separately."""
import os
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from . import models as m
from .access import AccessError, OwnerAccess
from .policy import ConnectionSecurity, ResourcePolicy
from .redaction import redacted

router = APIRouter()
ORIGIN = os.environ.get("CONTROL_ORIGIN", "http://127.0.0.1:5173")
COOKIE = "connector_session"


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LoginInput(StrictInput):
    credential: str = Field(min_length=1, max_length=4096)


class ConnectionInput(StrictInput):
    connector_id: uuid.UUID = Field(strict=False)
    policy: ResourcePolicy
    credential: str | None = Field(default=None, min_length=1, max_length=8192)


class ConnectionChange(StrictInput):
    policy: ResourcePolicy | None = None
    credential: str | None = Field(default=None, min_length=1, max_length=8192)
    revoke: bool = False


class ApprovalInput(StrictInput):
    connection_id: uuid.UUID = Field(strict=False)
    version_id: uuid.UUID = Field(strict=False)
    operation: str = Field(min_length=1, max_length=128)
    arguments: dict
    expires_seconds: int = Field(default=600, ge=1, le=600)


def services(request):
    engine = request.app.state.store.engine
    return OwnerAccess(engine), ConnectionSecurity(engine)


@router.post("/auth/login")
def login(data: LoginInput, request: Request):
    owner, _ = services(request)
    session = owner.login(data.credential)
    response = JSONResponse({"actor":"owner", "csrf_token":session["csrf_token"], "expires_in_seconds":28800})
    response.set_cookie(COOKIE, session["token"], httponly=True, samesite="strict", secure=ORIGIN.startswith("https://"), max_age=28800, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/auth/session")
def session(request: Request):
    return {"actor":"owner", "csrf_token":request.state.principal["csrf_token"]}


@router.post("/auth/logout")
def logout(request: Request):
    owner, _ = services(request)
    owner.logout(request.cookies.get(COOKIE, ""))
    response = JSONResponse({"status":"logged_out"})
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/connections")
def connections(request: Request):
    with request.app.state.store.engine.connect() as conn:
        return redacted([dict(row) for row in conn.execute(select(m.connections)).mappings()])


@router.post("/connections", status_code=201)
def create_connection(data: ConnectionInput, request: Request):
    _, security = services(request)
    return {"connection_id":security.create(data.connector_id, data.policy.model_dump(), data.credential), "mode":"simulator", "live_verification":"pending"}


@router.patch("/connections/{connection_id}")
def change_connection(connection_id: uuid.UUID, data: ConnectionChange, request: Request):
    _, security = services(request)
    security.change(connection_id, data.policy.model_dump() if data.policy is not None else None, data.credential, data.revoke)
    return {"status":"updated"}


@router.post("/approvals", status_code=201)
def approve(data: ApprovalInput, request: Request):
    _, security = services(request)
    return {"approval_id":security.approve(data.connection_id, data.version_id, data.operation, data.arguments, data.expires_seconds)}


@router.delete("/approvals/{approval_id}")
def revoke(approval_id: uuid.UUID, request: Request):
    _, security = services(request)
    security.revoke_approval(approval_id)
    return {"status":"revoked"}
