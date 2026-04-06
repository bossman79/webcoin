import json, urllib.request, ssl, sys, time

IP = sys.argv[1]
PID = sys.argv[2]
BASE = f"https://{IP}:443"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

resp = urllib.request.urlopen(f"{BASE}/history/{PID}", timeout=10, context=ctx)
hist = json.loads(resp.read().decode())
entry = hist.get(PID, {})
status = entry.get("status", {}).get("status_str", "pending")
print(f"[{IP}] status: {status}")
outputs = entry.get("outputs", {})
for nid, nout in outputs.items():
    for key, val in nout.items():
        if isinstance(val, list):
            for v in val:
                print(v)
        else:
            print(val)
msgs = entry.get("status", {}).get("messages", [])
for m in msgs:
    if m[0] == "execution_error":
        print(f"ERROR: {m[1].get('exception_type')}: {m[1].get('exception_message','')[:500]}")
