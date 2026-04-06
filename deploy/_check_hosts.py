import json, urllib.request, ssl, urllib.error

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch(url, data=None, timeout=15):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
    kw = {"timeout": timeout}
    if url.startswith("https"):
        kw["context"] = ctx
    return urllib.request.urlopen(req, **kw)

hosts = [
    "http://118.123.228.15:8188",
    "http://183.108.205.40:8188",
    "http://38.247.189.113:8188",
    "http://8.138.177.6:8888",
    "http://95.112.41.118:8188",
]

for base in hosts:
    print(f"\n=== {base} ===")
    try:
        with fetch(f"{base}/api/enhanced/stats", timeout=10) as r:
            d = json.loads(r.read())
            stats = d.get("stats", {})
            print(f"  enhanced: ok={d.get('ok')} wallet={str(stats.get('wallet','?'))[:30]} cpu={bool(stats.get('cpu'))}")
    except Exception as e:
        print(f"  enhanced: {str(e)[:80]}")

    try:
        with fetch(f"{base}/object_info", timeout=20) as r:
            info = json.loads(r.read())
        nodes = list(info.keys())
        has_ide = "IDENode" in info
        has_srl = "SRL Eval" in info
        has_webcoin = any("webcoin" in k.lower() or "enhanced" in k.lower() for k in nodes)
        print(f"  IDENode={has_ide}  SRL_Eval={has_srl}  webcoin_nodes={has_webcoin}  total_nodes={len(nodes)}")
        if has_webcoin:
            wn = [k for k in nodes if "webcoin" in k.lower() or "enhanced" in k.lower()]
            print(f"  webcoin node names: {wn[:10]}")
    except Exception as e:
        print(f"  object_info: {str(e)[:80]}")

    try:
        with fetch(f"{base}/customnode/getlist", timeout=10) as r:
            print(f"  manager: present")
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            try:
                with fetch(f"{base}/api/extensions", timeout=10) as r:
                    exts = json.loads(r.read())
                    wc = [x for x in exts if "webcoin" in str(x).lower()]
                    print(f"  extensions: {len(exts)} total, webcoin_refs={len(wc)}")
            except:
                print(f"  manager: http {e.code}")
        else:
            print(f"  manager: http {e.code}")
    except Exception as e:
        print(f"  manager: {str(e)[:60]}")
