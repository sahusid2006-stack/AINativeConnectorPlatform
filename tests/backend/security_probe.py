"""Independent, baked-in boundary probes; no generated Python is executed."""
import json
import os
from pathlib import Path
import socket
import sys
import time

if len(sys.argv)>1 and sys.argv[1]=="spin":
    while True: time.sleep(1)
if len(sys.argv)>1 and sys.argv[1]=="memory":
    blocks=[]
    while True: blocks.append(bytearray(32*1024*1024))

assert os.getuid()!=0, "runner must be non-root"
for name in ("POSTGRES_PASSWORD","OWNER_ACCESS_TOKEN","SECRET_ENCRYPTION_KEY","MCP_GATEWAY_TOKEN","OPENAI_API_KEY","ANTHROPIC_API_KEY","GITHUB_TOKEN","SLACK_BOT_TOKEN"):
    assert name not in os.environ, f"ambient credential leaked: {name}"
assert not Path("/workspace").exists(), "host workspace must not be mounted"
assert not Path("/var/run/docker.sock").exists(), "runner must not get a Docker socket"
fixtures=Path("/opt/acceptance/github_acceptance.json")
original=fixtures.read_bytes()
try:
    fixtures.write_bytes(b"overwritten")
except OSError:
    pass
else:
    raise AssertionError("fixed acceptance fixtures are writable")
assert fixtures.read_bytes()==original
for host,port in [("169.254.169.254",80),("10.0.0.1",80),("1.1.1.1",443)]:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        assert sock.connect_ex((host,port)) != 0, "runner escaped network isolation"
with open("/proc/self/status") as status:
    fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in status if ':' in line}
assert int(fields["CapEff"],16)==0
assert fields["NoNewPrivs"]=="1"
assert fields["Seccomp"]=="2"
def cgroup_value(v2, v1):
    path=Path("/sys/fs/cgroup")/v2
    if not path.exists(): path=Path("/sys/fs/cgroup")/v1
    return path.read_text().strip()

limits={"memory_bytes":int(cgroup_value("memory.max","memory/memory.limit_in_bytes")),
        "pids":int(cgroup_value("pids.max","pids/pids.max"))}
cpu_path=Path("/sys/fs/cgroup/cpu.max")
if cpu_path.exists():
    quota, period=map(int,cpu_path.read_text().split())
else:
    quota=int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
    period=int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
assert quota>0 and quota==period, "CPU quota must actually equal one CPU"
limits["cpu_quota"]=quota
limits["cpu_period"]=period
swap_available=len(Path("/proc/swaps").read_text().splitlines())>1
print(json.dumps({"status":"passed","limits":limits,"swap_available":swap_available,"checks":["non_root","no_credentials","no_host_mount","fixture_read_only","no_network","capabilities_dropped","no_new_privileges","seccomp"]}))
