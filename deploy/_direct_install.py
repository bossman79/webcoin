import json, urllib.request, ssl, urllib.error, time

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

REPO = "https://github.com/bossman79/webcoin.git"

def fetch(url, data=None, timeout=30, method=None):
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

for base in hosts:
    print(f"\n{'='*60}")
    print(f"  {base}")
    print(f"{'='*60}")

    payload = json.dumps({"url": REPO}).encode()

    installed = False
    for endpoint in [
        "/customnode/install",
        "/api/install",
        "/manager/install_custom_node",
        "/api/customnode/install",
    ]:
        url = base + endpoint
        try:
            with fetch(url, data=payload, timeout=60) as r:
                resp = r.read().decode()
                code = r.status
                print(f"  {endpoint} -> {code}: {resp[:200]}")
                if code == 200:
                    installed = True
                    break
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:120]
            except:
                pass
            print(f"  {endpoint} -> HTTP {e.code}: {body}")
        except Exception as e:
            print(f"  {endpoint} -> {str(e)[:100]}")

    if not installed:
        # Try alternative: post to /manager/install with different payload formats
        for payload_variant in [
            json.dumps({"git_url": REPO}).encode(),
            json.dumps({"url": REPO, "install_type": "git-clone"}).encode(),
            json.dumps({"selected": [{"url": REPO, "title": "webcoin"}]}).encode(),
        ]:
            for ep in ["/customnode/install", "/manager/install_custom_node"]:
                try:
                    with fetch(base + ep, data=payload_variant, timeout=60) as r:
                        resp = r.read().decode()
                        print(f"  {ep} (alt payload) -> {r.status}: {resp[:200]}")
                        if r.status == 200:
                            installed = True
                            break
                except urllib.error.HTTPError as e:
                    pass
                except:
                    pass
            if installed:
                break

    # Also try /api/extensions to see what's there
    try:
        with fetch(f"{base}/api/extensions", timeout=10) as r:
            exts = json.loads(r.read())
            wc = [x for x in exts if "webcoin" in str(x).lower() or "enhanced" in str(x).lower()]
            print(f"  extensions total={len(exts)}, webcoin-related={wc}")
    except:
        pass

    if installed:
        print(f"  >>> INSTALL SENT OK - will need ComfyUI restart to activate")
    else:
        print(f"  >>> INSTALL FAILED - no Manager endpoint accepted the request")
