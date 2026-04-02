"""Check a specific prompt result."""
import urllib.request, json, sys

ip = sys.argv[1]
pid = sys.argv[2]
base = f"http://{ip}:8188"

req = urllib.request.Request(f"{base}/history/{pid}", method="GET")
with urllib.request.urlopen(req, timeout=10) as r:
    entry = json.loads(r.read().decode()).get(pid, {})
    status = entry.get("status", {}).get("status_str", "pending")
    print(f"Status: {status}")
    outputs = entry.get("outputs", {})
    for nid, nout in outputs.items():
        for key, val in nout.items():
            if isinstance(val, list):
                for v in val:
                    print(v)
    if status == "error":
        msgs = entry.get("status", {}).get("messages", [])
        for m in msgs:
            if m[0] == "execution_error":
                err = m[1]
                etype = err.get("exception_type", "")
                emsg = str(err.get("exception_message", ""))[:300]
                print(f"Error: {etype}: {emsg}")
