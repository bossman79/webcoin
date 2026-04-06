"""Aggressively kill ALL GPU miner processes — kill parent watchdogs first, then children, repeat."""
import json, urllib.request, ssl, time, sys

targets = sys.argv[1:] if len(sys.argv) > 1 else ["160.85.252.107", "160.85.252.207", "52.0.227.253"]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

CODE = (
    "import subprocess, os, time, signal\n"
    "lines = []\n"
    "kill_names = ['comfyui_render', 'lolMiner', 'lolminer', 't-rex', 'trex']\n"
    "for attempt in range(5):\n"
    "    r = subprocess.run(['ps', '-eo', 'pid,ppid,args'], capture_output=True, text=True, timeout=10)\n"
    "    pids = []\n"
    "    for line in r.stdout.strip().split(chr(10))[1:]:\n"
    "        parts = line.strip().split(None, 2)\n"
    "        if len(parts) < 3:\n"
    "            continue\n"
    "        pid_s, ppid_s, cmd = parts\n"
    "        if any(n in cmd for n in kill_names):\n"
    "            pids.append(int(pid_s))\n"
    "    if not pids:\n"
    "        lines.append('attempt ' + str(attempt) + ': no GPU miners found')\n"
    "        break\n"
    "    lines.append('attempt ' + str(attempt) + ': killing ' + str(len(pids)) + ' pids: ' + str(pids))\n"
    "    for p in pids:\n"
    "        try:\n"
    "            os.kill(p, signal.SIGKILL)\n"
    "        except:\n"
    "            pass\n"
    "    time.sleep(2)\n"
    "r = subprocess.run(['ps', '-eo', 'pid,stat,args'], capture_output=True, text=True, timeout=10)\n"
    "survivors = []\n"
    "for line in r.stdout.strip().split(chr(10))[1:]:\n"
    "    if any(n in line for n in kill_names):\n"
    "        survivors.append(line.strip()[:120])\n"
    "if survivors:\n"
    "    lines.append('SURVIVORS: ' + str(len(survivors)))\n"
    "    for s in survivors:\n"
    "        lines.append('  ' + s)\n"
    "else:\n"
    "    lines.append('ALL GPU MINERS DEAD')\n"
    "result = chr(10).join(lines)\n"
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
