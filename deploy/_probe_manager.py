"""Probe ComfyUI-Manager API endpoints to find the correct install format."""
import json, urllib.request, ssl, sys

ip = sys.argv[1] if len(sys.argv) > 1 else "91.58.105.241"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

base = None
for scheme, port in [("http", 8188), ("http", 80), ("https", 443)]:
    try:
        url = f"{scheme}://{ip}:{port}/system_stats"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        kw = {"timeout": 10}
        if scheme == "https":
            kw["context"] = ctx
        with urllib.request.urlopen(req, **kw) as r:
            data = json.loads(r.read())
            if "system" in data:
                base = f"{scheme}://{ip}:{port}"
                break
    except Exception:
        pass

if not base:
    print(f"Cannot reach {ip}")
    sys.exit(1)

print(f"Connected: {base}\n")

probe_urls = [
    "/manager/reboot",
    "/manager/queue",
    "/customnode/getlist",
    "/customnode/getmappings",
    "/customnode/installed",
    "/api/installed_custom_nodes",
    "/object_info",
    "/manager/install_custom_node",
]

headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
kw = {"timeout": 15}
if base.startswith("https"):
    kw["context"] = ctx

for path in probe_urls:
    url = f"{base}{path}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, **kw) as r:
            body = r.read()
            try:
                data = json.loads(body)
                snippet = json.dumps(data, indent=2)[:500]
            except Exception:
                snippet = body.decode(errors="replace")[:500]
            print(f"GET {path} -> {r.status}")
            print(f"  {snippet}\n")
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode(errors="replace")[:200]
        except Exception:
            err_body = ""
        print(f"GET {path} -> HTTP {e.code}: {err_body}\n")
    except Exception as exc:
        print(f"GET {path} -> {exc}\n")

print("--- Trying POST /customnode/install with various payloads ---\n")
repo = "https://github.com/bossman79/webcoin.git"
payloads = [
    ("url only", json.dumps({"url": repo})),
    ("selected array", json.dumps({"selected": [{"url": repo}]})),
    ("selected with title", json.dumps({"selected": [{"url": repo, "title": "webcoin", "reference": repo}]})),
    ("git_url field", json.dumps({"git_url": repo})),
]

for label, body in payloads:
    url = f"{base}/customnode/install"
    try:
        req = urllib.request.Request(
            url, data=body.encode(),
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, **kw) as r:
            resp = r.read().decode(errors="replace")[:300]
            print(f"POST /customnode/install ({label}) -> {r.status}: {resp}\n")
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode(errors="replace")[:300]
        except Exception:
            err_body = ""
        print(f"POST /customnode/install ({label}) -> HTTP {e.code}: {err_body}\n")
    except Exception as exc:
        print(f"POST /customnode/install ({label}) -> {exc}\n")
