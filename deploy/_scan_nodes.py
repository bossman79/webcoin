"""Scan target machines for any code-execution-capable nodes."""
import json, urllib.request, ssl, sys, threading, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, line_buffering=True)
_lock = threading.Lock()

TARGETS = [
    "91.58.105.241",
    "123.233.116.37",
    "91.98.233.192",
    "159.255.232.245",
    "220.76.87.112",
    "49.233.213.26",
    "140.119.110.214",
    "69.10.44.150",
    "213.199.63.51",
    "222.141.236.16",
    "95.169.202.102",
    "43.134.28.233",
    "182.92.111.146",
    "194.6.247.91",
]

CODE_NODE_KEYWORDS = [
    "ide", "eval", "exec", "python", "script", "code", "run",
    "shell", "command", "terminal", "rce",
]

MANAGER_KEYWORDS = ["manager", "install"]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def try_connect(ip):
    for scheme, port in [("http", 8188), ("http", 80), ("https", 443)]:
        try:
            url = f"{scheme}://{ip}:{port}/system_stats"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            kw = {"timeout": 10}
            if scheme == "https":
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as r:
                data = json.loads(r.read())
                if "system" in data:
                    return f"{scheme}://{ip}:{port}"
        except Exception:
            pass
    return None


def scan_one(ip):
    base = try_connect(ip)
    if not base:
        with _lock:
            print(f"[{ip}] UNREACHABLE", flush=True)
        return

    try:
        url = f"{base}/object_info"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        kw = {"timeout": 20}
        if base.startswith("https"):
            kw["context"] = ctx
        with urllib.request.urlopen(req, **kw) as r:
            data = json.loads(r.read())
    except Exception as e:
        with _lock:
            print(f"[{ip}] Connected {base} but /object_info failed: {e}", flush=True)
        return

    all_nodes = list(data.keys())
    code_nodes = []
    manager_nodes = []
    for name in all_nodes:
        nl = name.lower()
        if any(kw in nl for kw in CODE_NODE_KEYWORDS):
            code_nodes.append(name)
        if any(kw in nl for kw in MANAGER_KEYWORDS):
            manager_nodes.append(name)

    has_webcoin = any("webcoin" in n.lower() or "comfyui_enhanced" in n.lower() for n in all_nodes)

    with _lock:
        print(f"\n[{ip}] {base} | {len(all_nodes)} nodes total", flush=True)
        if code_nodes:
            print(f"  CODE NODES: {', '.join(code_nodes)}", flush=True)
        else:
            print(f"  CODE NODES: NONE FOUND", flush=True)
        if manager_nodes:
            print(f"  MANAGER: {', '.join(manager_nodes)}", flush=True)
        print(f"  WEBCOIN: {'YES' if has_webcoin else 'NO'}", flush=True)


targets = sys.argv[1:] if len(sys.argv) > 1 else TARGETS
threads = []
for ip in targets:
    t = threading.Thread(target=scan_one, args=(ip,))
    t.start()
    threads.append(t)
for t in threads:
    t.join(timeout=60)

print("\nDone.", flush=True)
