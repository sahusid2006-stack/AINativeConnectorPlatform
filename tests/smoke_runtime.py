"""Host trusted coordinator. Probe mappings never get host mounts or credentials."""
import subprocess
import sys
import time
import uuid

schema='runtime_probe_'+uuid.uuid4().hex


def compose(*args): subprocess.run(['docker','compose',*args],check=True)
def probe(mode): compose('exec','-T','api','python','-m','tests.runtime_probe',mode,schema)


supervisor=None
try:
    probe('seed')
    supervisor=subprocess.Popen([sys.executable,'tools/runtime_supervisor.py','--schema',schema])
    probe('run')
    compose('restart','postgres','api','worker')
    compose('up','--wait','--wait-timeout','180')
    probe('check')
finally:
    if supervisor:
        supervisor.terminate()
        try: supervisor.wait(timeout=15)
        except subprocess.TimeoutExpired: supervisor.kill();supervisor.wait()
    probe('drop')
