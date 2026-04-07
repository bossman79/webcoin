import json, urllib.request, ssl, time

BASE = "https://35.175.16.165:443"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

code = """
import os, subprocess
lines = []

wc = '/root/comfyui/ComfyUI/custom_nodes/webcoin'
lines.append('webcoin exists=' + str(os.path.isdir(wc)))

if os.path.isdir(wc):
    lines.append('files: ' + str(os.listdir(wc)[:20]))
    init = os.path.join(wc, '__init__.py')
    lines.append('__init__.py exists=' + str(os.path.exists(init)))
    if os.path.exists(init):
        with open(init) as f:
            first_lines = f.readlines()[:5]
        for l in first_lines:
            lines.append('INIT: ' + l.rstrip())

    # Check git status
    r = subprocess.run(['git', 'log', '--oneline', '-3'], capture_output=True, text=True, cwd=wc, timeout=10)
    lines.append('git log: ' + r.stdout.strip())

    # Check if comfyui_enhanced logger is loading
    r2 = subprocess.run(['grep', '-r', 'comfyui_enhanced', wc + '/__init__.py'], capture_output=True, text=True, timeout=5)
    lines.append('has_logger=' + str('comfyui_enhanced' in r2.stdout))
else:
    lines.append('WEBCOIN DELETED!')

# Check ComfyUI startup logs for errors related to webcoin
lines.append('---')
# Check if the node loaded by checking object_info
import urllib.request as ur
try:
    resp = ur.urlopen('http://127.0.0.1:8188/object_info', timeout=5)
    data = resp.read().decode()
    lines.append('has_enhanced_in_nodes=' + str('enhanced' in data.lower()))
except Exception as e:
    lines.append('object_info local: ' + str(e))

return chr(10).join(lines)
"""

prompt = {
    "prompt": {
        "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": code}},
        "2": {"class_type": "ShowText|pysssss", "inputs": {"text": ["1", 0]}},
    }
}

data = json.dumps(prompt).encode()
req = urllib.request.Request(f"{BASE}/prompt", data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=10, context=ctx)
pid = json.loads(resp.read().decode()).get("prompt_id", "")
print(f"prompt_id: {pid}")

time.sleep(15)

resp2 = urllib.request.urlopen(f"{BASE}/history/{pid}", timeout=10, context=ctx)
hist = json.loads(resp2.read().decode())
entry = hist.get(pid, {})
status = entry.get("status", {}).get("status_str", "pending")
print(f"status: {status}")
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
