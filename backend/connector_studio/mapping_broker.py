"""Trusted DB queue to a fixed-image host supervisor. Payloads contain data only."""
from datetime import timedelta
import json
import re
import sys
import time
import uuid

from sqlalchemy import event, select, update
from . import models as m
from .access import AccessError
from .database import database_engine
from .mapping_eval import bounded
from .repositories import EvidenceStore
from .redaction import redacted


class MappingBroker:
    def __init__(self,engine): self.engine,self.store=engine,EvidenceStore(engine)
    def evaluate(self,payload,deadline):
        bounded(payload)
        task=uuid.uuid4()
        with self.engine.begin() as conn:
            conn.execute(m.mapping_tasks.insert().values(id=task,payload=redacted(payload),deadline=self.store.now(conn)+timedelta(seconds=max(0,deadline-time.monotonic()))))
        while time.monotonic()<deadline:
            with self.engine.connect() as conn:
                row=conn.execute(select(m.mapping_tasks).where(m.mapping_tasks.c.id==task)).mappings().one()
            if row['status']=='completed': return bounded(row['result'])
            if row['status']=='failed': raise AccessError('timeout' if isinstance(row['result'],dict) and row['result'].get('category')=='timeout' else 'unsupported_capability',422)
            time.sleep(.03)
        raise AccessError('timeout',504)


def claim(engine):
    store=EvidenceStore(engine)
    with engine.begin() as conn:
        now=store.now(conn)
        conn.execute(update(m.mapping_tasks).where(m.mapping_tasks.c.status.in_(['queued','running']),m.mapping_tasks.c.deadline<=now).values(status='failed',result={'category':'timeout'},lease_token=None,lease_until=None))
        conn.execute(update(m.mapping_tasks).where(m.mapping_tasks.c.status=='running',m.mapping_tasks.c.lease_until<=now,m.mapping_tasks.c.deadline>now).values(status='queued',lease_token=None,lease_until=None))
        row=conn.execute(select(m.mapping_tasks).where(m.mapping_tasks.c.status=='queued',m.mapping_tasks.c.deadline>now).order_by(m.mapping_tasks.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
        if not row: return None
        token=uuid.uuid4();remaining=(row['deadline']-now).total_seconds()
        conn.execute(update(m.mapping_tasks).where(m.mapping_tasks.c.id==row['id']).values(status='running',lease_token=token,lease_until=min(row['deadline'],now+timedelta(seconds=10))))
        return {'id':str(row['id']),'lease_token':str(token),'remaining':min(10,remaining),'payload':row['payload']}


def finish(engine,data):
    bounded(data)
    with engine.begin() as conn:
        now=EvidenceStore.now(conn)
        result=data.get('result')
        status='completed' if data.get('status')=='completed' else 'failed'
        conn.execute(update(m.mapping_tasks).where(m.mapping_tasks.c.id==uuid.UUID(data['id']),m.mapping_tasks.c.status=='running',m.mapping_tasks.c.lease_token==uuid.UUID(data['lease_token']),m.mapping_tasks.c.lease_until>now,m.mapping_tasks.c.deadline>now).values(status=status,result=redacted(result),lease_token=None,lease_until=None))


def main():
    action=sys.argv[1];schema=sys.argv[2] if len(sys.argv)>2 else 'public'
    if not re.fullmatch(r'public|runtime_probe_[0-9a-f]{32}',schema): raise SystemExit('Unsupported supervisor namespace')
    engine=database_engine()
    if schema!='public':
        @event.listens_for(engine,'connect')
        def namespace(dbapi,_):
            dbapi.autocommit=True
            with dbapi.cursor() as cursor: cursor.execute(f'SET search_path TO "{schema}"')
            dbapi.autocommit=False
    try:
        if action=='claim':
            EvidenceStore(engine).heartbeat('mapping_supervisor')
            EvidenceStore(engine).recover()
            print(json.dumps(claim(engine)))
        elif action=='finish': finish(engine,json.loads(sys.stdin.buffer.read(2*1024*1024+1)))
        else: raise SystemExit('Unsupported supervisor action')
    finally: engine.dispose()


if __name__=='__main__': main()
