"""Owner-only retained intake/proposals. No model calls or arbitrary file reads."""
import hashlib
import json
from typing import Annotated
import re
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Request
from pydantic import Field
from sqlalchemy import select

from . import models as m
from .access import AccessError
from .control import StrictInput
from .policy import ConnectionSecurity
from .redaction import redacted, remember
from .source_fetch import MAX_BYTES, fetch_source
from .source_parse import proposals, parse_document, unresolved

router=APIRouter()


class FetchInput(StrictInput):
    url: str = Field(min_length=1,max_length=4096)
    approved_origins: list[str] = Field(min_length=1,max_length=10)


class ProposalInput(StrictInput):
    source_ids: list[Annotated[uuid.UUID, Field(strict=False)]] = Field(min_length=1,max_length=10)


class SelectionInput(StrictInput):
    operation_ids: list[str] = Field(min_length=1,max_length=100)


def hydrate_redaction(engine):
    # Trusted redaction path only, not driver authority. No decrypted values leave this function.
    security=ConnectionSecurity(engine)
    key=security._key()
    with engine.connect() as conn:
        for row in conn.execute(select(m.connection_secrets)).mappings():
            try:
                value=AESGCM(key).decrypt(row["ciphertext"][:12],row["ciphertext"][12:],str(row["connection_id"]).encode()).decode()
            except Exception:
                raise AccessError("source_redaction_unavailable",503) from None
            remember(value)


def sanitize(text):
    text=redacted(text)
    text=re.sub(r"\b(?:sk-ant-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{10,}","[REDACTED]",text)
    text=re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9_.~+/=-]{16,}",r"\1[REDACTED]",text)
    text=re.sub(r'''(?im)((?:["']?)(?:password|api[_-]?key|access_token|credential_value|secret)(?:["']?)\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[A-Za-z0-9_./+-]+)''',r'\1"[REDACTED]"',text)
    return text


def retain(store, requested_origin, final_origin, mime_type, content, approved_origins=None):
    if len(content)>MAX_BYTES: raise AccessError("source_byte_limit",413)
    hydrate_redaction(store.engine)
    try: original=content.decode("utf-8")
    except UnicodeError: raise AccessError("source_utf8_required",422) from None
    clean=sanitize(original).encode()
    if len(clean)>MAX_BYTES: raise AccessError("source_byte_limit",413)
    # Parse before retaining, never silently retain truncated or unsafe structured data.
    parse_document(clean,mime_type)
    source_id=uuid.uuid4()
    with store.engine.begin() as conn:
        conn.execute(m.sources.insert().values(id=source_id,origin=sanitize(final_origin),mime_type=mime_type,content=clean,digest=hashlib.sha256(clean).hexdigest()))
        conn.execute(m.source_intake.insert().values(source_id=source_id,original_digest=hashlib.sha256(content).hexdigest(),redacted=clean!=content,requested_origin=sanitize(requested_origin),approved_origins=approved_origins or []))
        store.audit(conn,"owner","source.retained",source_id,{"redacted":clean!=content})
    return source_id


def propose(store, source_ids):
    if not 1<=len(source_ids)<=10 or len(set(source_ids))!=len(source_ids): raise AccessError("source_count_limit",422)
    with store.engine.connect() as conn:
        rows=list(conn.execute(select(m.sources).join(m.source_intake, m.source_intake.c.source_id==m.sources.c.id).where(m.sources.c.id.in_(source_ids))).mappings())
    if len(rows)!=len(source_ids): raise AccessError("source_not_found",404)
    operations, missing=[],[]
    for row in sorted(rows,key=lambda row:str(row["id"])):
        items, gaps=proposals(row["content"],row["mime_type"])
        missing.extend([{**gap,"source_id":str(row["id"])} for gap in gaps])
        for index,item in enumerate(items):
            item["id"]=f'{row["id"]}:{index}'
            for evidence in item["evidence"]: evidence["source_id"]=str(row["id"])
            operations.append(item)
    if len(operations)>100: raise AccessError("source_operation_limit",422)
    for item in operations:
        peers=[other for other in operations if (other["facts"]["method"],other["facts"]["path"])==(item["facts"]["method"],item["facts"]["path"])]
        if any(other["facts"]!=item["facts"] for other in peers): item["unresolved"].append(unresolved("noncontradictory endpoint/schema/authentication facts",item["name"]))
    document={"operations":operations,"unresolved":missing,"status":"blocked" if missing or any(item["unresolved"] for item in operations) else "ready_for_selection","extraction":"deterministic","source_instructions":"data_only"}
    proposal_id=uuid.uuid4()
    with store.engine.begin() as conn:
        conn.execute(m.source_proposals.insert().values(id=proposal_id,source_ids=[str(value) for value in source_ids],document=document))
        store.audit(conn,"owner","source.proposed",proposal_id)
    return {"proposal_id":proposal_id,**document}


def choose(store, proposal_id, operation_ids):
    if not operation_ids or len(set(operation_ids))!=len(operation_ids): raise AccessError("invalid_operation_selection",422)
    with store.engine.begin() as conn:
        document=conn.scalar(select(m.source_proposals.c.document).where(m.source_proposals.c.id==proposal_id))
        if document is None: raise AccessError("proposal_not_found",404)
        by_id={item["id"]:item for item in document["operations"]}
        if document["unresolved"] or any(value not in by_id or by_id[value]["unresolved"] for value in operation_ids): raise AccessError("source_facts_unresolved",422)
        selection_id=uuid.uuid4()
        conn.execute(m.source_selections.insert().values(id=selection_id,proposal_id=proposal_id,operation_ids=operation_ids))
        store.audit(conn,"owner","source.selected",selection_id)
        return {"selection_id":selection_id,"status":"selected","generation":"not_started"}


@router.post("/sources/upload",status_code=201)
async def upload(request: Request):
    mime=request.headers.get("content-type","text/plain").split(";")[0].lower()
    name=request.headers.get("x-source-name","uploaded-source")
    if len(name)>256 or any(ord(c)<32 for c in name): raise AccessError("invalid_source_name",422)
    source_id=retain(request.app.state.store,"upload://"+name,"upload://"+name,mime,await request.body())
    return {"source_id":source_id,"status":"retained"}


@router.post("/sources/fetch",status_code=201)
def fetch(data: FetchInput,request: Request):
    hydrate_redaction(request.app.state.store.engine)
    try: final,mime,content=fetch_source(data.url,data.approved_origins)
    except AccessError as error:
        return {"status":"blocked","unresolved":[{"fact":"source","category":error.category,"input_needed":"Provide an accessible supported source at an approved public destination or upload the document."}],"generation":"not_started"}
    return {"source_id":retain(request.app.state.store,data.url,final,mime,content,data.approved_origins),"status":"retained"}


@router.get("/sources/{source_id}")
def read_source(source_id: uuid.UUID,request: Request):
    with request.app.state.store.engine.connect() as conn:
        row=conn.execute(select(m.sources).where(m.sources.c.id==source_id)).mappings().first()
        meta=conn.execute(select(m.source_intake).where(m.source_intake.c.source_id==source_id)).mappings().first()
    if row is None: raise AccessError("source_not_found",404)
    hydrate_redaction(request.app.state.store.engine)
    return {"source_id":source_id,"origin":redacted(row["origin"]),"retrieved_at":row["created_at"],"digest":row["digest"],"mime_type":row["mime_type"],"content":sanitize(row["content"].decode()),"redacted":meta["redacted"] if meta else False}


@router.post("/sources/proposals",status_code=201)
def create_proposal(data: ProposalInput,request: Request):
    return propose(request.app.state.store,data.source_ids)


@router.get("/sources/proposals/{proposal_id}")
def read_proposal(proposal_id: uuid.UUID,request: Request):
    with request.app.state.store.engine.connect() as conn:
        row=conn.execute(select(m.source_proposals).where(m.source_proposals.c.id==proposal_id)).mappings().first()
    if row is None: raise AccessError("proposal_not_found",404)
    hydrate_redaction(request.app.state.store.engine)
    # Redaction string replacement, not field-name masking of schema declarations.
    return {"proposal_id":proposal_id,**json.loads(sanitize(json.dumps(row["document"])))}


@router.post("/sources/proposals/{proposal_id}/selection",status_code=201)
def select_operations(proposal_id: uuid.UUID,data: SelectionInput,request: Request):
    return choose(request.app.state.store,proposal_id,data.operation_ids)
