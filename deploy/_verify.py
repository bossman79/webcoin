"""Verify webcoin install on remote machine via IDENode."""
import json, urllib.request, time, sys

base = f"http://{sys.argv[1]}:8188"

verify_code = r"""
import os, platform
result = []
cn = None
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
    else:
        cn = os.path.join(os.path.dirname(folder_paths.__file__), 'custom_nodes')
except:
    for g in [r'C:\Users\u88ni\Desktop\comfyui\custom_nodes', '/root/ComfyUI/custom_nodes']:
        if os.path.isdir(g):
            cn = g
            break
result.append(f'cn={cn}')
result.append(f'os={platform.system()}')

target = os.path.join(cn, 'webcoin')
result.append(f'exists={os.path.exists(target)}')
result.append(f'isdir={os.path.isdir(target)}')
if os.path.isdir(target):
    files = os.listdir(target)
    result.append(f'files={files}')
    init = os.path.join(target, '__init__.py')
    result.append(f'init_exists={os.path.exists(init)}')
    core = os.path.join(target, 'core')
    result.append(f'core_exists={os.path.isdir(core)}')
    if os.path.isdir(core):
        result.append(f'core_files={os.listdir(core)}')
    git = os.path.join(target, '.git')
    result.append(f'has_git={os.path.isdir(git)}')
else:
    result.append('webcoin dir NOT FOUND')

result = '\n'.join(result)
"""

prompt = {
    "prompt": {
        "1": {
            "class_type": "IDENode",
            "inputs": {"pycode": verify_code, "language": "python"}
        },
        "2": {
            "class_type": "PreviewTextNode",
            "inputs": {"text": ["1", 0]}
        }
    }
}

body = json.dumps(prompt).encode()
req = urllib.request.Request(base + "/prompt", data=body, headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode())
    pid = data.get("prompt_id")
    print(f"prompt_id: {pid}")

time.sleep(5)

req2 = urllib.request.Request(f"{base}/history/{pid}", method="GET")
with urllib.request.urlopen(req2, timeout=10) as r:
    hist = json.loads(r.read().decode())
    entry = hist.get(pid, {})
    print(f"Status: {entry.get('status', {}).get('status_str')}")
    outputs = entry.get("outputs", {})
    for nid, nout in outputs.items():
        for key, val in nout.items():
            print(f"  [{nid}] {key}: {val}")
