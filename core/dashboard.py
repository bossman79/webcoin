"""
Dashboard stats poller.

Polls XMRig HTTP API and pushes stats to WebSocket clients connected
via ComfyUI's /ws/enhanced endpoint (registered in __init__.py).

Falls back to standalone WebSocket on port 44881 if the ComfyUI route
wasn't registered.
"""

import asyncio
import json
import logging
import threading
import time

logger = logging.getLogger("comfyui_enhanced")

POLL_INTERVAL = 5
FALLBACK_WS_PORT = 44881

try:
    import websockets
    import websockets.server
except ImportError:
    websockets = None


class DashboardServer:
    def __init__(self, miner_mgr, config_builder=None, ws_port: int = FALLBACK_WS_PORT):
        self.miner = miner_mgr
        self.config_builder = config_builder
        self.ws_port = ws_port
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        from __init__ import _ws_clients, _latest_stats
        self._comfy_clients = _ws_clients
        self._comfy_stats = True

        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="dashboard-poll")
        self._thread.start()
        logger.info("Dashboard polling started")

    def stop(self) -> None:
        self._running = False

    def _poll_loop(self) -> None:
        import __init__ as _init

        while self._running:
            time.sleep(POLL_INTERVAL)
            summary = self.miner.get_summary()
            if summary:
                stats = self._extract_stats(summary)
                _init._latest_stats.update(stats)
                self._push_to_comfy_clients(_init._ws_clients, stats)

    def _push_to_comfy_clients(self, clients, stats):
        if not clients:
            return

        payload = json.dumps({"type": "stats", "data": stats})
        dead = set()

        for ws in clients.copy():
            try:
                loop = ws._req.loop if hasattr(ws, '_req') else None
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        ws.send_str(payload), loop
                    )
            except Exception:
                dead.add(ws)
        clients -= dead

    @staticmethod
    def _extract_stats(summary: dict) -> dict:
        hashrate = summary.get("hashrate", {})
        totals = hashrate.get("total", [0, 0, 0])
        connection = summary.get("connection", {})
        cpu = summary.get("cpu", {})
        results = summary.get("results", {})

        return {
            "hostname": summary.get("worker_id", "unknown"),
            "uptime": summary.get("uptime", 0),
            "hashrate_now": totals[0] if totals else 0,
            "hashrate_1m": totals[1] if len(totals) > 1 else 0,
            "hashrate_15m": totals[2] if len(totals) > 2 else 0,
            "hashrate_max": hashrate.get("highest", 0),
            "algo": summary.get("algo", ""),
            "pool": connection.get("pool", ""),
            "accepted": results.get("shares_good", 0),
            "rejected": results.get("shares_total", 0) - results.get("shares_good", 0),
            "diff_current": results.get("diff_current", 0),
            "cpu_brand": cpu.get("brand", ""),
            "cpu_cores": cpu.get("cores", 0),
            "cpu_threads": cpu.get("threads", 0),
            "version": summary.get("version", ""),
        }
