"""Host-only fixed-image launcher. No socket mount or credentials in mapping containers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
IMAGE='connector-studio-evaluator'


def command(*args,**kwargs):
    return subprocess.run(args,cwd=ROOT,text=True,capture_output=True,**kwargs)


def sandbox(payload,deadline=10):
    name='connector-eval-'+uuid.uuid4().hex
    args=['docker','run','--name',name,'--interactive','--network','none','--read-only','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges:true','--cpus','1','--memory','256m','--memory-swap','256m','--pids-limit','64','--tmpfs','/tmp:rw,noexec,nosuid,size=16m',IMAGE,'python','-m','connector_studio.mapping_eval']
    try:
        completed=command(*args,input=json.dumps(payload),timeout=max(.1,deadline))
        inspected=command('docker','inspect',name,timeout=5)
        config=json.loads(inspected.stdout)[0];host=config['HostConfig']
        assert host['NetworkMode']=='none' and host['ReadonlyRootfs'] and host['Memory']==256*1024*1024
        assert host['NanoCpus']==1000000000 and host['PidsLimit']==64 and not host.get('Binds')
        assert all(mount['Type']=='tmpfs' and mount['Destination']=='/tmp' for mount in config['Mounts'])
        assert not any(item.split('=',1)[0] in {'ANTHROPIC_API_KEY','POSTGRES_PASSWORD','OWNER_ACCESS_TOKEN','SECRET_ENCRYPTION_KEY','MCP_GATEWAY_TOKEN'} for item in config['Config']['Env'])
        if host['MemorySwap']!=host['Memory']:
            # Same measured no-swap fallback as the existing security gate.
            proof=command('docker','run','--rm','--network','none',IMAGE,'python','-c',"print(len(open('/proc/swaps').read().splitlines()) == 1)",timeout=5)
            assert proof.stdout.strip()=='True'
        if completed.returncode or len(completed.stdout.encode())>2*1024*1024: raise ValueError()
        result=json.loads(completed.stdout)
        assert result['status'] in {'completed','failed'}
        return result
    except Exception:
        return {'status':'failed','result':{'category':'timeout'}}
    finally:
        command('docker','rm','--force',name,timeout=10)


def bridge(action,schema,data=None):
    return command('docker','compose','exec','-T','worker','python','-m','connector_studio.mapping_broker',action,schema,input=json.dumps(data) if data else None,timeout=15)


def execute(task,schema):
    result=sandbox(task['payload'],task['remaining'])
    bridge('finish',schema,{**result,'id':task['id'],'lease_token':task['lease_token']})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--schema',default='public');args=parser.parse_args()
    print('Fixed mapping supervisor started; five bounded slots, no credential logging.',flush=True)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures=[]
        while True:
            futures=[future for future in futures if not future.done()]
            if len(futures)<5:
                result=bridge('claim',args.schema)
                try: task=json.loads(result.stdout) if result.returncode==0 else None
                except ValueError: task=None
                if task: futures.append(pool.submit(execute,task,args.schema))
            time.sleep(.05 if len(futures)<5 else .1)


if __name__=='__main__': main()
