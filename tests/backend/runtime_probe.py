"""Real isolated mappings + trusted provider calls, in a disposable schema."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
import re
import sys
import uuid
import urllib.request

from sqlalchemy import event, select, text, update
from connector_studio import models as m
from connector_studio.database import database_engine, migrate
from connector_studio.repositories import EvidenceStore
from connector_studio.policy import ConnectionSecurity
from connector_studio.rest_runtime import Runtime, RestDriver
from tests.runtime_support import test_package
from tests.security_support import POLICY

mode,schema=sys.argv[1:]
assert re.fullmatch(r'runtime_probe_[0-9a-f]{32}',schema)
admin=database_engine()
if mode=='drop':
    with admin.begin() as conn: conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    raise SystemExit(0)
if mode=='seed':
    with admin.begin() as conn: conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
engine=database_engine()
@event.listens_for(engine,'connect')
def namespace(dbapi,_):
    dbapi.autocommit=True
    with dbapi.cursor() as cursor: cursor.execute(f'SET search_path TO "{schema}"')
    dbapi.autocommit=False
store,security=EvidenceStore(engine),ConnectionSecurity(engine)


def fixture(path,data=None):
    request=urllib.request.Request('http://fixtures:8000'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=5) as response: return json.load(response)


if mode=='seed':
    migrate(engine)
    source=store.snapshot('fixture://runtime-probe','text/plain',b'Synthetic test contract')
    connector=store.connector('Handcrafted isolated runtime probe')
    version=store.candidate(connector,test_package(source),[source])
    with engine.begin() as conn:
        conn.execute(update(m.versions).where(m.versions.c.id==version).values(status='published',published_by='probe-owner',published_at=store.now(conn)))
    connection=security.create(connector,POLICY,'simulator-github-token')
    bad=security.create(connector,POLICY,'invalid-synthetic-token')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE runtime_probe_ids (version_id uuid, connection_id uuid, bad_connection_id uuid)')
        conn.execute(text('INSERT INTO runtime_probe_ids VALUES (:version,:connection,:bad)'),{'version':version,'connection':connection,'bad':bad})
elif mode=='run':
    with engine.connect() as conn: ids=conn.execute(text('SELECT * FROM runtime_probe_ids')).mappings().one()
    version,connection=ids['version_id'],ids['connection_id']
    runtime=Runtime(engine)
    fixture('/_fixture/reset',{'docs_revision':'v1','provider_revision':'v1'})
    base={'owner':'demo','repo':'connector-fixture'}
    page1=runtime.invoke(connection,version,'github_list_issues_v1',{**base,'page':1,'per_page':2})
    assert page1['status']=='succeeded',page1
    assert [row['number'] for row in page1['output']['issues']]==[1] and page1['output']['next_page']==2
    page2=runtime.invoke(connection,version,'github_list_issues_v1',{**base,'page':2,'per_page':2})
    assert [row['number'] for row in page2['output']['issues']]==[3,4] and page2['output']['next_page'] is None
    get=runtime.invoke(connection,version,'github_get_issue_v1',{**base,'issue_number':1})
    assert get['output']['title']=='Demo issue 1'
    invalid=runtime.invoke(connection,version,'github_get_issue_v1',{**base,'issue_number':0})
    assert invalid['error']['category']=='validation' and invalid['attempts']==0
    for conn_id,number,scenario,expected in ((ids['bad_connection_id'],1,None,'authentication'),(connection,999,None,'not_found'),(connection,1,'permission','permission'),(connection,1,'rate_limit','rate_limit'),(connection,1,'upstream_error','upstream')):
        result=runtime.invoke(conn_id,version,'github_get_issue_v1',{**base,'issue_number':number},scenario=scenario)
        assert result['error']['category']==expected,result
        assert result['attempts']==(3 if scenario in {'rate_limit','upstream_error'} else 1)
    write_args={**base,'title':'Actual isolated write, not a static result'};op='github_create_issue_v1'
    denied=runtime.invoke(connection,version,op,write_args)
    assert denied['status']=='approval_required' and fixture('/_fixture/state')['writes']==0
    approval=security.approve(connection,version,op,write_args)
    created=runtime.invoke(connection,version,op,write_args,approval)
    assert created['status']=='succeeded' and created['output']['number']==5 and created['output']['title']==write_args['title']
    assert runtime.invoke(connection,version,op,write_args,approval)['status']=='approval_required'
    fixture('/_fixture/reset',{'docs_revision':'v1','provider_revision':'v1'})
    class ShortIOBudget(RestDriver):
        def send(self,credential,deadline,pinned):
            import time
            return super().send(credential,min(deadline,time.monotonic()+.4),pinned)
    unknown_runtime=Runtime(engine,driver_factory=ShortIOBudget)
    approval=security.approve(connection,version,op,write_args)
    unknown=unknown_runtime.invoke(connection,version,op,write_args,approval,scenario='ambiguous_write')
    assert unknown['status']=='outcome_unknown' and unknown['attempts']==1,unknown
    assert fixture('/_fixture/state')['writes']==1
    assert unknown_runtime.invoke(connection,version,op,write_args,approval)['error']['detail']=='write_reconciliation_required'
    fresh=security.approve(connection,version,op,write_args)
    assert unknown_runtime.invoke(connection,version,op,write_args,fresh)['attempts']==0
    assert fixture('/_fixture/state')['writes']==1
    # Five concurrent ordinary reads share the bounded isolated supervisor.
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(lambda _:runtime.invoke(connection,version,'github_get_issue_v1',{**base,'issue_number':1}),range(5)))
    assert all(result['status']=='succeeded' for result in results),results
    with engine.connect() as conn:
        assert conn.scalar(select(m.mapping_tasks.c.id).where(m.mapping_tasks.c.status=='completed'))
        assert conn.scalar(select(m.invocations.c.id).where(m.invocations.c.status=='outcome_unknown'))
    print('Handcrafted mappings: actual isolated list/get/approved-create, pagination/PR exclusion, normalized failures/retries, uncertain write, and five concurrent reads passed. Not genuine generation or publication acceptance.')
elif mode=='check':
    with engine.connect() as conn:
        rows=list(conn.execute(select(m.invocations)).mappings())
        assert rows and any(row['status']=='outcome_unknown' for row in rows)
        assert all(row['version_id'] and row['trace_id'] for row in rows)
        assert conn.scalar(select(m.mapping_tasks.c.id).where(m.mapping_tasks.c.status=='completed'))
    print('Exact-version traces, mapping evidence and unknown write survived process/database restart.')
else: raise SystemExit('Unsupported probe mode')
engine.dispose();admin.dispose()
