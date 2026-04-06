"""Kill GPU miners on specified machines."""
import json, urllib.request, ssl, time, sys

targets = sys.argv[1:] if len(sys.argv) > 1 else ["160.85.252.107", "160.85.252.207"]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

CODE = (
    "import subprocess, os\n"
    "lines = []\n"
    "for n in ['comfyui_render', 't-rex', 'trex', 'lolMiner']:\n"
    "    try:\n"
    "        r = subprocess.run(['pkill', '-9', '-f', n], capture_output=True, text=True, timeout=5)\n"
    "        if r.returncode == 0:\n"
    "            lines.append('killed ' + n)\n"
    "    except:\n"
    "        pass\n"
    "import time; time.sleep(2)\n"
    "r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=10)\n"
    "gpu_procs = [l.strip()[:120] for l in r.stdout.split(chr(10))\n"
    "             if any(x in l for x in ['comfyui_render', 't-rex', 'lolMiner']) and 'grep' not in l]\n"
    "if gpu_procs:\n"
    "    lines.append('still running: ' + str(len(gpu_procs)))\n"
    "    for p in gpu_procs:\n"
    "        lines.append('  ' + p)\n"
    "else:\n"
    "    lines.append('GPU miners stopped')\n"
    "result = chr(10).join(lines) if lines else 'no GPU miners found'\n"
)

for ip in targets:
    base = f"https://{ip}:443"
    body = json.dumps({
        "prompt": {
            "1": {"class_type": "IDENode", "inputs": {"pycode": CODE, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        }
    }).encode()
    req = urllib.request.Request(f"{base}/prompt", data=body, headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, context=ctx, timeout=15).read().decode())
        pid = resp["prompt_id"]
        time.sleep(8)
        req2 = urllib.request.Request(f"{base}/history/{pid}")
        entry = json.loads(urllib.request.urlopen(req2, context=ctx, timeout=10).read().decode()).get(pid, {})
        outputs = entry.get("outputs", {})
        texts = []
        for nid, nout in outputs.items():
            for key, val in nout.items():
                if isinstance(val, list):
                    texts.extend(str(v) for v in val)
        print(f"=== {ip} ===")
        print("\n".join(texts) if texts else "no output")
    except Exception as e:
        print(f"=== {ip} === ERROR: {e}")
    print()
