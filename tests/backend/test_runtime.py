from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from connector_studio import models as m
from connector_studio.access import AccessError
from connector_studio.mapping_eval import evaluate, MappingError
from connector_studio.rest_runtime import Runtime, RestDriver, ProviderError, public_schema, retry_delay
from tests.runtime_support import test_package
from tests.security_support import SecurityBase, POLICY, OWNER

ARGS={'owner':'demo','repo':'connector-fixture','issue_number':1}
ISSUE={'number':1,'title':'Dynamic title','body':None,'state':'open','html_url':'https://example.com/1'}


class LocalEvaluator:
    """Unit-test fake, never the API's production evaluator."""
    def evaluate(self,payload,deadline): return evaluate(payload)


class ProviderFake:
    responses=[];calls=0;resources={'destination':'http://fixtures:8000','repository':'demo/connector-fixture'}
    def __init__(self,request,operation,policy,scenario=None): pass
    def guard(self,deadline): return None
    def send(self,credential,deadline,pinned):
        type(self).calls+=1
        response=type(self).responses.pop(0)
        if isinstance(response,Exception): raise response
        return response,None


class MappingTests(unittest.TestCase):
    def test_request_paths_defaults_and_native_list_filter(self):
        package=test_package('00000000-0000-0000-0000-000000000001')
        mapping=package['mappings'][0]
        request=evaluate({'mode':'request','mapping':mapping,'input':{'owner':'demo','repo':'connector-fixture','page':1,'per_page':2}})
        self.assertEqual(request['url'],'http://fixtures:8000/repos/demo/connector-fixture/issues?page=1&per_page=2')
        output=evaluate({'mode':'output','mapping':mapping,'native':[ISSUE,{**ISSUE,'pull_request':{}}],'next_page':2})
        self.assertEqual(output,{'issues':[ISSUE],'next_page':2})

    def test_no_env_code_attribute_or_remote_reference_operators(self):
        mapping=test_package('00000000-0000-0000-0000-000000000001')['mappings'][1]
        for path in ('/{input.__class__}','/{owner!r}','//169.254.169.254/x','/x?token=secret'):
            altered=deepcopy(mapping);altered['request']['path_template']=path
            with self.assertRaises(MappingError): evaluate({'mode':'request','mapping':altered,'input':ARGS})
        # Unknown objects are data, not executed; trusted request validation rejects them.
        altered=deepcopy(mapping);altered['request']['json']={'eval':'__import__("os").system("echo unsafe")'}
        with self.assertRaises(MappingError): evaluate({'mode':'request','mapping':altered,'input':ARGS})

    def test_limits_missing_values_and_invalid_each(self):
        mapping=test_package('00000000-0000-0000-0000-000000000001')['mappings'][1]
        with self.assertRaises(MappingError): evaluate({'mode':'output','mapping':mapping,'native':{}})
        with self.assertRaises(MappingError): evaluate({'mode':'request','mapping':mapping,'input':{'deep':[[[[0]]]]*10001}})

    def test_trusted_actual_resource_derivation_blocks_path_scope_forgery(self):
        for url in ('http://fixtures:8000/_fixture/reset','http://fixtures:8000/repos/demo/../issues/1','http://fixtures:8000/repos/demo/connector-fixture/issues/1?x=1','http://169.254.169.254/repos/demo/connector-fixture/issues/1'):
            with self.assertRaises(AccessError): RestDriver({'method':'GET','url':url,'json':None},'github_get_issue_v1',POLICY)
        driver=RestDriver({'method':'GET','url':'http://fixtures:8000/repos/demo/connector-fixture/issues?page=1&per_page=2','json':None},'github_list_issues_v1',POLICY)
        self.assertEqual(driver.next_page('<http://fixtures:8000/repos/demo/connector-fixture/issues?page=2&per_page=2>; rel="next"'),2)
        with self.assertRaises(ProviderError): driver.next_page('<http://other/repos/demo/connector-fixture/issues?page=2>; rel="next"')

    def test_retry_after_seconds_dates_and_large_delay(self):
        self.assertEqual(retry_delay('2',1),2)
        self.assertGreaterEqual(retry_delay(None,2),.2)
        self.assertEqual(retry_delay('Thu, 01 Jan 1970 00:00:00 GMT',1),0)


class RuntimeTests(SecurityBase):
    def setUp(self):
        super().setUp();ProviderFake.calls=0;ProviderFake.responses=[ISSUE]
        source=self.store.snapshot('fixture://runtime','text/plain',b'Synthetic test contract')
        connector=self.store.connector('Runtime tests')
        self.package=test_package(source)
        self.version=self.store.candidate(connector,self.package,[source])
        with self.engine.begin() as conn:
            conn.execute(update(m.versions).where(m.versions.c.id==self.version).values(status='published',published_by='test-owner',published_at=self.store.now(conn)))
        self.connection_id=self.security.create(connector,POLICY,'simulator-github-token')
        self.runtime=Runtime(self.engine,LocalEvaluator(),ProviderFake,sleep=lambda _:None)

    def call(self,operation='github_get_issue_v1',args=None,approval=None):
        return self.runtime.invoke(self.connection_id,self.version,operation,args if args is not None else ARGS,approval)

    def test_succeeded_exact_trace_and_provider_data_not_static_results(self):
        result=self.call();self.assertEqual(result['status'],'succeeded');self.assertEqual(result['output']['title'],'Dynamic title')
        with self.engine.connect() as conn:
            row=conn.execute(select(m.invocations).where(m.invocations.c.trace_id==result['trace_id'])).mappings().one()
        self.assertEqual(str(row['version_id']),result['version_id']);self.assertEqual(row['metadata']['verification_mode'],'simulator')

    def test_invalid_input_and_denied_policy_never_contact_provider(self):
        result=self.call(args={**ARGS,'issue_number':0});self.assertEqual(result['error']['category'],'validation');self.assertEqual(ProviderFake.calls,0)
        self.security.change(self.connection_id,policy={**POLICY,'repositories':['other/repo']})
        result=self.call();self.assertEqual(result['error']['category'],'permission');self.assertEqual(ProviderFake.calls,0)

    def test_auth_permission_not_found_do_not_retry(self):
        for category in ('authentication','permission','not_found'):
            ProviderFake.calls=0;ProviderFake.responses=[ProviderError(category)]
            result=self.call();self.assertEqual(result['error']['category'],category);self.assertEqual(result['attempts'],1)

    def test_retryable_reads_bounded_and_honor_retry_after(self):
        delays=[];self.runtime.sleep=delays.append
        ProviderFake.responses=[ProviderError('rate_limit','1'),ProviderError('upstream'),ISSUE]
        result=self.call();self.assertEqual(result['status'],'succeeded');self.assertEqual(result['attempts'],3);self.assertEqual(delays[0],1)
        ProviderFake.responses=[ProviderError('upstream')]*3
        self.assertEqual(self.call()['attempts'],3)
        ProviderFake.responses=[ProviderError('rate_limit','999')]
        result=self.call();self.assertEqual(result['attempts'],1);self.assertEqual(result['error']['category'],'rate_limit')

    def test_write_requires_exact_approval_then_uncertain_write_never_retries(self):
        args={'owner':'demo','repo':'connector-fixture','title':'new'};op='github_create_issue_v1'
        result=self.call(op,args);self.assertEqual(result['status'],'approval_required');self.assertEqual(ProviderFake.calls,0)
        approval=self.security.approve(self.connection_id,self.version,op,args)
        ProviderFake.responses=[ProviderError('timeout')]
        result=self.call(op,args,approval);self.assertEqual(result['status'],'outcome_unknown');self.assertEqual(result['attempts'],1)
        blocked=self.call(op,args,approval);self.assertEqual(blocked['status'],'failed');self.assertEqual(blocked['error']['category'],'outcome_unknown');self.assertEqual(ProviderFake.calls,1)
        replacement=self.security.approve(self.connection_id,self.version,op,args)
        self.assertEqual(self.call(op,args,replacement)['error']['detail'],'write_reconciliation_required');self.assertEqual(ProviderFake.calls,1)
        with self.engine.connect() as conn: self.assertTrue(conn.scalar(select(m.jobs.c.write_intent).where(m.jobs.c.status=='outcome_unknown')))

    def test_interrupted_write_recovers_invocation_and_blocks_fresh_approval(self):
        from connector_studio.policy import arguments_digest
        op='github_create_issue_v1';args={'owner':'demo','repo':'connector-fixture','title':'crashed write'}
        trace,job,token=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
        with self.engine.begin() as conn:
            now=self.store.now(conn)
            conn.execute(m.invocations.insert().values(id=trace,version_id=self.version,trace_id=trace,status='running',metadata={'connection_id':str(self.connection_id),'operation':op,'arguments_digest':arguments_digest(args),'verification_mode':'simulator'}))
            conn.execute(m.jobs.insert().values(id=job,kind='write',status='running',payload={'invocation_id':str(trace)},deadline=now+timedelta(seconds=30),lease_owner='crashed',lease_token=token,lease_until=now-timedelta(seconds=1),attempt_count=1,write_intent=True))
            conn.execute(m.attempts.insert().values(job_id=job,attempt_number=1,lease_token=token,status='running'))
        approval=self.security.approve(self.connection_id,self.version,op,args)
        result=self.call(op,args,approval)
        self.assertEqual(result['error']['detail'],'write_reconciliation_required');self.assertEqual(ProviderFake.calls,0)
        with self.engine.connect() as conn:
            row=conn.execute(select(m.invocations).where(m.invocations.c.id==trace)).mappings().one()
            self.assertEqual(row['status'],'outcome_unknown');self.assertEqual(row['metadata']['result']['version_id'],str(self.version))
            self.assertEqual(row['metadata']['connection_id'],str(self.connection_id))

    def test_mapping_requests_cannot_substitute_approved_public_arguments(self):
        with self.engine.connect() as conn:
            connector=conn.scalar(select(m.versions.c.connector_id).where(m.versions.c.id==self.version))
        for index,key,value,operation,args in (
            (1,'path_template','/repos/demo/connector-fixture/issues/2','github_get_issue_v1',ARGS),
            (0,'query',{'page':2,'per_page':2},'github_list_issues_v1',{'owner':'demo','repo':'connector-fixture','page':1,'per_page':2}),
            (2,'json',{'title':'substituted title','body':None},'github_create_issue_v1',{'owner':'demo','repo':'connector-fixture','title':'approved title'})):
            package=deepcopy(self.package);package['mappings'][index]['request'][key]=value
            version=self.store.candidate(connector,package,package['source_snapshot_ids'],revision=index+2)
            with self.engine.begin() as conn: conn.execute(update(m.versions).where(m.versions.c.id==version).values(status='published',published_by='test',published_at=self.store.now(conn)))
            result=self.runtime.invoke(self.connection_id,version,operation,args)
            self.assertEqual(result['status'],'failed');self.assertEqual(result['attempts'],0)
        self.assertEqual(ProviderFake.calls,0)

    def test_mapping_broker_fences_completion_and_expires_tasks(self):
        from connector_studio.mapping_broker import claim, finish
        task=uuid.uuid4()
        with self.engine.begin() as conn:
            now=self.store.now(conn)
            conn.execute(m.mapping_tasks.insert().values(id=task,payload={},deadline=now+timedelta(seconds=20)))
        leased=claim(self.engine);self.assertEqual(leased['id'],str(task))
        completion={'id':str(task),'lease_token':str(uuid.uuid4()),'status':'completed','result':{'ok':True}}
        finish(self.engine,completion)
        with self.engine.connect() as conn: self.assertEqual(conn.scalar(select(m.mapping_tasks.c.status).where(m.mapping_tasks.c.id==task)),'running')
        completion['lease_token']=leased['lease_token'];finish(self.engine,completion)
        completion['result']={'ok':False};finish(self.engine,completion)
        with self.engine.connect() as conn: self.assertEqual(conn.scalar(select(m.mapping_tasks.c.result).where(m.mapping_tasks.c.id==task)),{'ok':True})
        expired=uuid.uuid4()
        with self.engine.begin() as conn: conn.execute(m.mapping_tasks.insert().values(id=expired,payload={},deadline=self.store.now(conn)-timedelta(seconds=1)))
        self.assertIsNone(claim(self.engine))
        with self.engine.connect() as conn: self.assertEqual(conn.scalar(select(m.mapping_tasks.c.result).where(m.mapping_tasks.c.id==expired)),{'category':'timeout'})

    def test_output_schema_failure_and_frozen_public_schemas(self):
        ProviderFake.responses=[{'number':1}]
        self.assertEqual(self.call()['status'],'failed')
        ProviderFake.responses=[{**ISSUE,'state':'unknown'}]
        self.assertEqual(self.call()['error']['detail'],'output_schema_mismatch')
        source=self.store.snapshot('fixture://tampered','text/plain',b'Synthetic test contract');connector=self.store.connector('Tampered')
        package=test_package(source);package['manifest']['operations'][1]['input_schema']={'type':'object'}
        version=self.store.candidate(connector,package,[source]);connection=self.security.create(connector,POLICY,'simulator-github-token')
        with self.engine.begin() as conn: conn.execute(update(m.versions).where(m.versions.c.id==version).values(status='published',published_by='test',published_at=self.store.now(conn)))
        result=self.runtime.invoke(connection,version,'github_get_issue_v1',ARGS)
        self.assertEqual(result['error']['category'],'unsupported_capability')

    def test_candidate_test_private_path_and_ordinary_route_denial(self):
        source=self.store.snapshot('fixture://candidate','text/plain',b'Synthetic test contract');connector=self.store.connector('Candidate')
        version=self.store.candidate(connector,test_package(source),[source]);connection=self.security.create(connector,POLICY,'simulator-github-token')
        with self.assertRaises(AccessError): self.runtime.invoke(connection,version,'github_get_issue_v1',ARGS)
        result=self.runtime.invoke(connection,version,'github_get_issue_v1',ARGS,candidate_test=True)
        self.assertEqual(result['status'],'succeeded')

    def test_http_owner_boundary_does_not_accept_testing_or_launch_flags(self):
        from connector_studio.main import app
        from connector_studio.control import ORIGIN
        with patch('connector_studio.main.database_engine',return_value=self.engine),TestClient(app,base_url='http://localhost') as client:
            headers={'Origin':ORIGIN}
            self.assertEqual(client.post('/invocations',json={},headers=headers).status_code,401)
            session=client.post('/auth/login',json={'credential':OWNER},headers=headers).json();headers['X-CSRF-Token']=session['csrf_token']
            data={'connection_id':str(self.connection_id),'version_id':str(self.version),'operation':'github_get_issue_v1','public_major':1,'arguments':ARGS,'candidate_test':True}
            self.assertEqual(client.post('/invocations',json=data,headers=headers).status_code,422)
