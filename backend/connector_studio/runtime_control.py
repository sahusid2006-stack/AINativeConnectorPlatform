"""Owner-only invocation; no candidate/test/scenario or container-launch inputs."""
from typing import Annotated, Literal
import uuid
from fastapi import APIRouter, Request
from pydantic import Field, model_validator
from sqlalchemy import select
from . import models as m
from .access import AccessError
from .control import StrictInput
from .rest_runtime import Runtime

router=APIRouter()


class InvocationInput(StrictInput):
    connection_id: uuid.UUID=Field(strict=False)
    operation: str=Field(min_length=1,max_length=128)
    public_major: int=Field(ge=1)
    arguments: dict
    version_id: uuid.UUID | None=Field(default=None,strict=False)
    selection: Literal['active'] | None=None
    approval_id: uuid.UUID | None=Field(default=None,strict=False)
    @model_validator(mode='after')
    def selector(self):
        if (self.version_id is None)==(self.selection is None): raise ValueError('Select exact or active, not both')
        return self


@router.post('/invocations')
def invoke(data: InvocationInput,request: Request):
    if data.selection=='active': raise AccessError('active_registry_pending',501)
    return Runtime(request.app.state.store.engine).invoke(data.connection_id,data.version_id,data.operation,data.arguments,data.approval_id,data.public_major)


@router.get('/invocations/{trace_id}')
def read_invocation(trace_id: uuid.UUID,request: Request):
    with request.app.state.store.engine.connect() as conn:
        row=conn.execute(select(m.invocations).where(m.invocations.c.trace_id==trace_id)).mappings().first()
    if not row: raise AccessError('invocation_not_found',404)
    return row['metadata']
