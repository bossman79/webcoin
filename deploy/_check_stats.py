"""Check if stats are available via HTTP and debug the data flow."""
import json, urllib.request, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "131.113.41.148"
base = f"http://{ip}:8188"

# Check HTTP stats endpoint
print("=== /api/enhanced/stats ===")
try:
    req = urllib.request.Request(f"{base}/api/enhanced/stats", method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
        print(json.dumps(data, indent=2)[:1000])
except Exception as e:
    print(f"ERROR: {e}")

# Check XMRig API directly
print("\n=== XMRig API (port 44880) ===")
try:
    req = urllib.request.Request(f"http://{ip}:44880/2/summary", method="GET",
                                headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode())
        print(f"Hashrate: {data.get('hashrate', {}).get('total', [])}")
        print(f"Algo: {data.get('algo')}")
        print(f"Uptime: {data.get('uptime')}")
except Exception as e:
    print(f"ERROR: {e}")

# Check lolMiner API directly
print("\n=== lolMiner API (port 44882) ===")
try:
    req = urllib.request.Request(f"http://{ip}:44882", method="GET",
                                headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode())
        print(f"Software: {data.get('Software')}")
        algos = data.get('Algorithms', [{}])
        if algos:
            print(f"Algo: {algos[0].get('Algorithm')}")
            print(f"Hashrate: {algos[0].get('Total_Performance')}")
except Exception as e:
    print(f"ERROR: {e}")
