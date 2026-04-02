import json, urllib.request, sys

base = f"http://{sys.argv[1]}:8188"
req = urllib.request.Request(f"{base}/history?max_items=3", method="GET")
with urllib.request.urlopen(req, timeout=10) as r:
    hist = json.loads(r.read().decode())
    for pid, entry in hist.items():
        print(f"\n=== Prompt {pid} ===")
        print(f"Status: {entry.get('status', {})}")
        outputs = entry.get("outputs", {})
        print(f"Outputs keys: {list(outputs.keys())}")
        for nid, nout in outputs.items():
            print(f"  Node {nid}: {json.dumps(nout)[:500]}")
