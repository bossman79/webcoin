"""
Install or complete webcoin (ComfyUI-Enhanced) on explicit host:port targets.

Does NOT restart ComfyUI, systemd, Docker, or the OS from this script — you
restart ComfyUI yourself. After each host finishes install/hotfix/miner steps,
the script waits 90 seconds so you can do that before it continues.

Usage:
  python deploy/_deploy_webcoin_hosts.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# host:port as provided (8888 is valid for some installs)
TARGET_SPECS = [
    # "64.247.206.73:8188",  # already attempted — debug later
    # "192.154.102.26:8188",  # already attempted — debug later
    "118.123.228.15:8188",
    "183.108.205.40:8188",
    "38.247.189.113:8188",
    "8.138.177.6:8888",
    "95.112.41.118:8188",
]

MANUAL_RESTART_WAIT_SEC = 90

_DEPLOY_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mass_deploy", _DEPLOY_DIR / "_mass_deploy.py")
_md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_md)


def _urlopen(req, **kw):
    if req.full_url.startswith("https"):
        kw.setdefault("context", _md.ctx)
    return urllib.request.urlopen(req, **kw)


def discover_base(host: str, port: int) -> str | None:
    for scheme in ("http", "https"):
        url = f"{scheme}://{host}:{port}/system_stats"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urlopen(req, timeout=18) as r:
                data = json.loads(r.read())
                if "system" in data:
                    return f"{scheme}://{host}:{port}"
        except Exception:
            continue
    return None


def probe_enhanced(base: str) -> tuple[bool, dict | None]:
    try:
        req = urllib.request.Request(
            base + "/api/enhanced/stats", headers={"User-Agent": "Mozilla/5.0"}
        )
        with _urlopen(req, timeout=14) as r:
            d = json.loads(r.read())
        if d.get("ok") and isinstance(d.get("stats"), dict):
            return True, d["stats"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, None
    except Exception:
        pass
    return False, None


def interrupt_clear(base: str) -> None:
    for path in ("/interrupt", "/api/interrupt"):
        try:
            _urlopen(
                urllib.request.Request(
                    base + path,
                    data=b"{}",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=12,
            )
        except Exception:
            pass
    try:
        _urlopen(
            urllib.request.Request(
                base + "/queue",
                data=json.dumps({"clear": True}).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            ),
            timeout=12,
        )
    except Exception:
        pass


def deploy_one(spec: str) -> None:
    host, sep, port_s = spec.partition(":")
    if not sep:
        print(f"[{spec}] BAD_SPEC (need host:port)", flush=True)
        return
    port = int(port_s)

    print(f"\n{'='*60}\n[{spec}]", flush=True)
    base = discover_base(host, port)
    if not base:
        print(f"[{spec}] UNREACHABLE (tried http/https on port {port})", flush=True)
        return

    print(f"[{spec}] base={base}", flush=True)
    had_api, stats = probe_enhanced(base)
    cpu_ok = bool(stats and stats.get("cpu"))
    print(
        f"[{spec}] /api/enhanced/stats: {'OK' if had_api else '404/missing'} "
        f"cpu_stats={'yes' if cpu_ok else 'no'}",
        flush=True,
    )

    interrupt_clear(base)
    time.sleep(1)

    print(f"[{spec}] [1/3] install / git webcoin...", flush=True)
    r1 = _md.install_node(base)
    print(f"[{spec}]     -> {str(r1)[:220]}", flush=True)

    print(f"[{spec}] [2/3] hotfix from GitHub raw...", flush=True)
    r2 = _md.run_code(base, _md.HOTFIX_CODE, wait_secs=100)
    print(f"[{spec}]     -> {str(r2)[:280]}", flush=True)

    print(f"[{spec}] [3/3] miner config + start...", flush=True)
    r3 = _md.run_code(base, _md.FIX_AND_START_CODE, wait_secs=100)
    print(f"[{spec}]     -> {str(r3)[:280]}", flush=True)

    print(f"[{spec}] {'-'*52}", flush=True)
    print(
        f"[{spec}] Manual step: restart ComfyUI on this host yourself if files changed.",
        flush=True,
    )
    print(
        f"[{spec}] This script does NOT run systemctl, docker restart, kill, or OS reboot.",
        flush=True,
    )
    print(
        f"[{spec}] Waiting {MANUAL_RESTART_WAIT_SEC}s — use this window to restart ComfyUI.",
        flush=True,
    )
    time.sleep(MANUAL_RESTART_WAIT_SEC)

    final_ok, final_st = probe_enhanced(base)
    print(
        f"[{spec}] FINAL (after your wait): enhanced_api={'YES' if final_ok else 'NO'} "
        f"has_cpu={bool((final_st or {}).get('cpu'))} — if NO, restart ComfyUI and check "
        f"{base}/api/enhanced/stats",
        flush=True,
    )


def main() -> None:
    print(
        "Webcoin deploy for %d targets — no remote ComfyUI/OS restart; %ds pause per host for YOUR restart."
        % (len(TARGET_SPECS), MANUAL_RESTART_WAIT_SEC),
        flush=True,
    )
    for spec in TARGET_SPECS:
        try:
            deploy_one(spec)
        except Exception as exc:
            print(f"[{spec}] EXCEPTION: {exc}", flush=True)
        time.sleep(2)
    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
