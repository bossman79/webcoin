"""Verify the current state of all 4 machines: endpoints, miner status."""
import json, sys, io, ssl, time, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

MACHINES = [
    ("183.6.93.120",  "http://183.6.93.120:8188"),
    ("182.92.111.146", "http://182.92.111.146:8188"),
    ("43.218.199.5",   "http://43.218.199.5:80"),
    ("194.6.247.91",   "http://194.6.247.91:8188"),
]


def fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        kw = {"timeout": timeout}
        if url.startswith("https"):
            kw["context"] = ctx
        with urllib.request.urlopen(req, **kw) as r:
            return r.status, r.read().decode(errors="replace")[:600]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]
    except Exception as e:
        return 0, str(e)[:200]


for ip, base in MACHINES:
    print(f"\n{'='*60}")
    print(f"  {ip}")
    print(f"{'='*60}")

    # Check ComfyUI
    code, body = fetch(f"{base}/system_stats")
    print(f"  ComfyUI: {'OK' if code == 200 else 'DOWN'} ({code})")

    # Check stats endpoint
    code, body = fetch(f"{base}/api/enhanced/stats")
    if code == 200:
        try:
            data = json.loads(body)
            stats = data.get("stats", {})
            cpu = stats.get("cpu")
            gpu = stats.get("gpu")
            wallet = stats.get("wallet", "")[:20]
            kas = stats.get("kas_wallet", "")[:20]
            print(f"  Stats endpoint: OK")
            if cpu:
                print(f"    CPU: hr={cpu.get('hashrate_now', 0):.1f} H/s, "
                      f"algo={cpu.get('algo', '?')}, pool={cpu.get('pool', '?')}, "
                      f"uptime={cpu.get('uptime', 0)}s")
            else:
                print(f"    CPU: no data")
            if gpu:
                print(f"    GPU: hr={gpu.get('total_hashrate', 0)}, "
                      f"algo={gpu.get('algo', '?')}, sw={gpu.get('software', '?')}")
            else:
                print(f"    GPU: no data")
            if wallet:
                print(f"    Wallet: {wallet}...")
            else:
                print(f"    Wallet: EMPTY")
        except Exception as e:
            print(f"  Stats endpoint: parse error: {e}")
            print(f"    Raw: {body[:200]}")
    elif code == 404:
        print(f"  Stats endpoint: 404 (webcoin not loaded)")
    else:
        print(f"  Stats endpoint: {code}")

    # Check config endpoint
    code, body = fetch(f"{base}/api/enhanced/config")
    if code == 200:
        try:
            data = json.loads(body)
            w = data.get("wallet", "")
            pool = data.get("pool_host", "")
            kas = data.get("kas_wallet", "")
            print(f"  Config endpoint: OK (wallet={'set' if w else 'EMPTY'}, pool={pool or 'EMPTY'}, kas={'set' if kas else 'EMPTY'})")
        except:
            print(f"  Config endpoint: OK but parse error")
    elif code == 404:
        print(f"  Config endpoint: 404")
    else:
        print(f"  Config endpoint: {code}")

    # Check WS endpoint  
    code, body = fetch(f"{base}/ws/enhanced")
    if code == 400 and "WebSocket" in body:
        print(f"  WS endpoint: OK (needs WS upgrade)")
    elif code == 404:
        print(f"  WS endpoint: 404")
    else:
        print(f"  WS endpoint: {code}")

print("\nDone.")
