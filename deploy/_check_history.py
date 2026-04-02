import json, urllib.request, sys

base = f"http://{sys.argv[1]}:8188"
req = urllib.request.Request(f"{base}/history?max_items=5", method="GET")
with urllib.request.urlopen(req, timeout=10) as r:
    hist = json.loads(r.read().decode())
    for pid, entry in hist.items():
        status = entry.get("status", {})
        status_str = status.get("status_str", "unknown")
        print(f"Prompt {pid}: {status_str}")
        outputs = entry.get("outputs", {})
        for nid, nout in outputs.items():
            if "text" in nout:
                for line in nout["text"]:
                    print(f"  [{nid}] {line[:500]}")
        if not outputs:
            msgs = status.get("messages", [])
            for m in msgs:
                print(f"  msg: {m}")
