"""Trusted simulator REST driver and exact-version invocation. Never calls a model."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import random
import re
import socket
from threading import BoundedSemaphore, Timer
import time
import uuid
from urllib.parse import parse_qs, unquote, urlsplit

from jsonschema import ValidationError
from sqlalchemy import select, update
from . import models as m
from .access import AccessError
from .contracts import CONTRACTS, offline_validator, validate_contract
from .mapping_broker import MappingBroker
from .mapping_eval import bounded
from .policy import ConnectionSecurity, arguments_digest
from .redaction import redacted
from .repositories import Claim, EvidenceStore, LostLease
from .source_fetch import DestinationGuard, MAX_BYTES, origin

_slots=BoundedSemaphore(5)
GITHUB=json.loads((CONTRACTS/'github.schema.json').read_text())
CAPABILITIES={'github_list_issues_v1':('ListInput','ListOutput','read','GET'),
              'github_get_issue_v1':('GetInput','Issue','read','GET'),
              'github_create_issue_v1':('CreateInput','Issue','write','POST')}


def public_schema(name):
    return {'$schema':GITHUB['$schema'],'$defs':deepcopy(GITHUB['$defs']),'$ref':'#/$defs/'+name}


class ProviderError(Exception):
    def __init__(self,category,retry_after=None):
        self.category,self.retry_after=category,retry_after
        super().__init__(category)


def retry_delay(value,attempt):
    if value is not None:
        try: return max(0,float(value))
        except (ValueError,TypeError):
            try: return max(0,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
            except (ValueError,TypeError,OverflowError): pass
    return min(2,.1*(2**(attempt-1)))+random.uniform(0,.1)


class RestDriver:
    def __init__(self,request,operation,policy,scenario=None):
        if set(request)!={'method','url','json'}: raise AccessError('unsupported_capability',422)
        self.request,self.operation,self.policy,self.scenario=request,operation,policy,scenario
        parsed=urlsplit(request['url'])
        if origin(request['url'])!='http://fixtures:8000': raise AccessError('live_verification_pending',403)
        # Native capability profile, not hardcoded response mappings. Derive scope from the real URL.
        match=re.fullmatch(r'/repos/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues(?:/([1-9][0-9]*))?',parsed.path)
        if not match or any(value in {'.','..'} for value in match.groups() if value): raise AccessError('resource_denied',403)
        owner,repo,number=match.groups()
        if request['method']!=CAPABILITIES[operation][3] or bool(number)!=(operation=='github_get_issue_v1'): raise AccessError('resource_denied',403)
        query=parse_qs(parsed.query,keep_blank_values=True,strict_parsing=True)
        if set(query)-{'page','per_page'} or any(len(values)!=1 for values in query.values()): raise AccessError('resource_denied',403)
        if operation!='github_list_issues_v1' and query: raise AccessError('resource_denied',403)
        for key,values in query.items():
            if not re.fullmatch(r'[1-9][0-9]*',values[0]) or key=='per_page' and int(values[0])>100: raise AccessError('validation',422)
        body=request['json']
        if request['method']=='POST' and (not isinstance(body,dict) or set(body)-{'title','body'} or not isinstance(body.get('title'),str)): raise AccessError('validation',422)
        if request['method']=='GET' and body is not None: raise AccessError('validation',422)
        self.resources={'destination':origin(request['url']),'repository':owner+'/'+repo}

    def guard(self,deadline):
        remaining=deadline-time.monotonic()
        if remaining<=0: raise ProviderError('timeout')
        try: return DestinationGuard(self.policy['destinations']).check(self.request['url'],min(5,remaining))
        except AccessError as error:
            raise ProviderError('permission' if 'denied' in error.category else 'timeout') from None

    def send(self,credential,deadline,pinned):
        parsed,address=pinned
        remaining=deadline-time.monotonic()
        if remaining<=0: raise ProviderError('timeout')
        connection=http.client.HTTPConnection(parsed.hostname,parsed.port or 80,timeout=remaining)
        sock,timer=None,None
        def interrupt():
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        try:
            sock=socket.create_connection((address,parsed.port or 80),timeout=remaining)
            timer=Timer(max(.01,deadline-time.monotonic()),interrupt);timer.daemon=True;timer.start()
            if ipaddress.ip_address(sock.getpeername()[0])!=ipaddress.ip_address(address): raise ProviderError('permission')
            connection.sock=sock
            target=urlsplit(self.request['url']);path=target.path+('?' + target.query if target.query else '')
            headers={'Authorization':'Bearer '+credential,'Accept-Encoding':'identity','Content-Type':'application/json'}
            # Only independent test orchestration supplies this; it is not a mapping/API input.
            if self.scenario: headers['X-Fixture-Scenario']=self.scenario
            body=json.dumps(self.request['json'],allow_nan=False).encode() if self.request['json'] is not None else None
            connection.request(self.request['method'],path,body=body,headers=headers)
            response=connection.getresponse()
            categories={401:'authentication',403:'permission',404:'not_found',429:'rate_limit'}
            if response.status in categories: raise ProviderError(categories[response.status],response.getheader('Retry-After'))
            # Never follow redirects with credentials. All 3xx fail before target contact.
            if response.status//100!=2: raise ProviderError('upstream',response.getheader('Retry-After'))
            if response.getheader('Content-Encoding','identity') not in {'identity',''}: raise ProviderError('upstream')
            length=response.getheader('Content-Length')
            if length and int(length)>MAX_BYTES: raise ProviderError('upstream')
            raw=bytearray()
            while True:
                part=response.read1(min(65536,MAX_BYTES+1-len(raw)))
                if not part: break
                raw.extend(part)
                if len(raw)>MAX_BYTES: raise ProviderError('upstream')
            if length and len(raw)!=int(length): raise ProviderError('upstream')
            native=bounded(json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError())))
            return redacted(native),self.next_page(response.getheader('Link'))
        except ProviderError: raise
        except (TimeoutError,OSError,http.client.HTTPException): raise ProviderError('timeout') from None
        except (ValueError,TypeError): raise ProviderError('upstream') from None
        finally:
            if timer: timer.cancel()
            connection.close()
            if sock: sock.close()

    def next_page(self,link):
        if not link: return None
        if not isinstance(link,str) or len(link)>8192: raise ProviderError('upstream')
        links=re.findall(r'<([^>]+)>\s*;\s*rel="next"',link)
        if not links: return None
        if len(links)!=1: raise ProviderError('upstream')
        current,target=urlsplit(self.request['url']),urlsplit(links[0])
        try:
            if origin(links[0])!=origin(self.request['url']) or target.path!=current.path: raise ValueError()
            query=parse_qs(target.query,strict_parsing=True)
            if set(query)-{'page','per_page'} or len(query.get('page',[]))!=1 or not re.fullmatch(r'[1-9][0-9]*',query['page'][0]): raise ValueError()
            current_query=parse_qs(current.query)
            if query.get('per_page',current_query.get('per_page'))!=current_query.get('per_page'): raise ValueError()
            page=int(query['page'][0])
            if page<=int(current_query.get('page',['1'])[0]): raise ValueError()
            return page
        except (ValueError,AccessError): raise ProviderError('upstream') from None


class Runtime:
    def __init__(self,engine,evaluator=None,driver_factory=RestDriver,sleep=time.sleep):
        self.engine,self.store=engine,EvidenceStore(engine)
        self.security=ConnectionSecurity(engine)
        self.evaluator=evaluator or MappingBroker(engine)
        self.driver_factory,self.sleep=driver_factory,sleep

    def invoke(self,connection_id,version_id,operation,arguments,approval_id=None,public_major=1,*,candidate_test=False,scenario=None):
        if not _slots.acquire(blocking=False): raise AccessError('runtime_busy',429)
        try: return self._invoke(connection_id,version_id,operation,arguments,approval_id,public_major,candidate_test,scenario)
        finally: _slots.release()

    def _invoke(self,connection_id,version_id,operation,arguments,approval_id,public_major,candidate_test,scenario):
        with self.engine.connect() as conn:
            version=conn.execute(select(m.versions).where(m.versions.c.id==version_id)).mappings().first()
            connection=conn.execute(select(m.connections).where(m.connections.c.id==connection_id)).mappings().first()
        if not version or not connection: raise AccessError('version_or_connection_not_found',404)
        if version['connector_id']!=connection['connector_id'] or version['public_major']!=public_major or version['status']!='published' and not (candidate_test and version['status']=='candidate' and connection['mode']=='simulator'):
            raise AccessError('version_or_operation_denied',403)
        trace=uuid.uuid4();attempts=0;claim=None;dispatched=False
        result={'trace_id':str(trace),'version_id':str(version_id),'status':'failed','attempts':0}
        with self.engine.begin() as conn:
            conn.execute(m.invocations.insert().values(id=trace,version_id=version_id,trace_id=trace,status='running',metadata={'operation':operation,'verification_mode':'simulator','candidate_test':candidate_test}))
            self.store.audit(conn,'owner','invocation.started',trace,{'version_id':str(version_id)})
        try:
            package=bounded(json.loads(version['artifact']))
            if hashlib.sha256(version['artifact']).hexdigest()!=version['digest']: raise AccessError('unsupported_capability',422)
            contracts=[item for item in package['manifest']['operations'] if item['name']==operation]
            mappings=[item for item in package['mappings'] if item['operation']==operation]
            if operation not in CAPABILITIES or len(contracts)!=1 or len(mappings)!=1: raise AccessError('unsupported_capability',422)
            contract,mapping=contracts[0],mappings[0];input_name,output_name,classification,_=CAPABILITIES[operation]
            if contract['input_schema']!=public_schema(input_name) or contract['output_schema']!=public_schema(output_name) or contract['classification']!=classification or contract['protocol_mapping']!='rest' or mapping['driver']!='rest': raise AccessError('unsupported_capability',422)
            arguments_digest(arguments)
            offline_validator(public_schema(input_name)).validate(arguments)
            args=deepcopy(arguments)
            for key,schema in GITHUB['$defs'][input_name]['properties'].items():
                if key not in args and 'default' in schema: args[key]=schema['default']
            deadline=time.monotonic()+min(30,contract['timeout_seconds'])
            request=self.evaluator.evaluate({'mode':'request','mapping':mapping,'input':args},deadline)
            driver=self.driver_factory(request,operation,connection['resource_policy'],scenario)
            permit=self.security.permit(connection_id,version_id,operation,arguments,driver.resources,approval_id,_candidate_test=candidate_test)
            pinned=driver.guard(deadline)
            credential=self.security.resolve(permit)
            if permit.write:
                with self.engine.begin() as conn:
                    now=self.store.now(conn);token,job=uuid.uuid4(),uuid.uuid4()
                    conn.execute(m.jobs.insert().values(id=job,kind='write',status='running',payload={'invocation_id':str(trace),'version_id':str(version_id),'operation':operation},deadline=now+timedelta(seconds=30),lease_owner='rest-runtime',lease_token=token,lease_until=now+timedelta(seconds=30),attempt_count=1,write_intent=True))
                    conn.execute(m.attempts.insert().values(job_id=job,attempt_number=1,lease_token=token,status='running'))
                    self.store.audit(conn,'owner','write.intent',job)
                    claim=Claim(job,token,'rest-runtime',1)
            maximum=1 if permit.write else min(3,contract['retry_policy']['max_attempts']) if contract['retry_policy']['safe'] else 1
            while True:
                attempts+=1
                try:
                    dispatched=True
                    native,next_page=driver.send(credential,deadline,pinned)
                    break
                except ProviderError as error:
                    if permit.write or attempts>=maximum or error.category not in {'rate_limit','upstream','timeout'}: raise
                    delay=retry_delay(error.retry_after,attempts)
                    if not delay<deadline-time.monotonic(): raise
                    self.sleep(delay)
                    pinned=driver.guard(deadline)
                    credential=self.security.resolve(permit)
            output=self.evaluator.evaluate({'mode':'output','mapping':mapping,'input':args,'native':native,'next_page':next_page},deadline)
            offline_validator(public_schema(output_name)).validate(output)
            result.update(status='succeeded',output=redacted(output))
            if claim: self.store.finish(claim,'completed',{'invocation_id':str(trace),'status':'completed'})
        except (ValidationError,ValueError,TypeError):
            result['error']={'category':'outcome_unknown' if claim and dispatched else 'validation'}
        except AccessError as error:
            result['status']='approval_required' if error.category=='approval_required' else 'failed'
            category='permission' if error.category in {'resource_denied','connection_changed','version_or_operation_denied'} else 'authentication' if error.category=='reconnection_required' else 'timeout' if error.category=='timeout' else 'unsupported_capability'
            result['error']={'category':category}
        except ProviderError as error:
            result['error']={'category':error.category}
        except Exception:
            result['error']={'category':'upstream'}
        if claim and result['status']!='succeeded':
            result['status']='outcome_unknown';result['error']={'category':'outcome_unknown','reconciliation_required':True}
            try: self.store.finish(claim,'outcome_unknown',{'invocation_id':str(trace),'category':'outcome_unknown'})
            except LostLease: self.store.recover()
        result['attempts']=attempts
        validate_contract('InvocationResult',result)
        with self.engine.begin() as conn:
            conn.execute(update(m.invocations).where(m.invocations.c.id==trace).values(status='completed' if result['status']=='succeeded' else result['status'],metadata=redacted({'operation':operation,'verification_mode':'simulator','candidate_test':candidate_test,'result':result})))
            self.store.audit(conn,'owner','invocation.finished',trace,{'status':result['status'],'attempts':attempts})
        return result
