"""Get error details from execution history."""
import urllib.request, json, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "111.199.97.153"
base = f"http://{ip}:8188"

req = urllib.request.Request(f"{base}/history?max_items=5", method="GET")
with urllib.request.urlopen(req, timeout=10) as r:
    hist = json.loads(r.read().decode())

for pid, entry in hist.items():
    status = entry.get("status", {})
    status_str = status.get("status_str", "unknown")
    print(f"\n=== {pid} [{status_str}] ===")
    msgs = status.get("messages", [])
    for m in msgs:
        if m[0] == "execution_error":
            err = m[1]
            print(f"  node_type: {err.get('node_type')}")
            print(f"  node_id: {err.get('node_id')}")
            print(f"  exception_type: {err.get('exception_type')}")
            print(f"  exception_message: {str(err.get('exception_message', ''))[:500]}")
            tb = err.get("traceback", "")
            if isinstance(tb, list):
                tb = "\n".join(tb)
            print(f"  traceback: {str(tb)[-500:]}")
