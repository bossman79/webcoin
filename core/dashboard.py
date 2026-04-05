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

from core.gpu_miner import DEFAULT_API_PORT, fetch_lolminer_http_summary

POLL_INTERVAL = 5
FALLBACK_WS_PORT = 44881

try:
    import websockets
    import websockets.server
except ImportError:
    websockets = None


class DashboardServer:
    def __init__(self, miner_mgr, config_builder=None, gpu_miner=None,
                 ws_port: int = FALLBACK_WS_PORT,
                 ws_clients=None, latest_stats=None, event_loop_getter=None):
        self.miner = miner_mgr
        self.gpu_miner = gpu_miner
        self.config_builder = config_builder
        self.ws_port = ws_port
        self._running = False
        self._thread: threading.Thread | None = None
        self._shared_clients = ws_clients
        self._shared_stats = latest_stats
        self._get_event_loop = event_loop_getter

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="dashboard-poll")
        self._thread.start()
        logger.info("Dashboard polling started")

    def stop(self) -> None:
        self._running = False

    def _poll_loop(self) -> None:
        while self._running:
            time.sleep(POLL_INTERVAL)

            combined = {"cpu": None, "gpu": None}

            if self.config_builder:
                combined["wallet"] = self.config_builder.get_wallet()
                gpu_cfg = self.config_builder.build_gpu_config()
                combined["kas_wallet"] = gpu_cfg.get("wallet", "")

            cpu_summary = self.miner.get_summary()
            if cpu_summary:
                combined["cpu"] = self._extract_stats(cpu_summary)

            # Prefer GPUMinerManager from orchestration; else poll lolMiner HTTP (e.g. GPU
            # started via SRL deploy — DashboardServer.gpu_miner was never set).
            gpu_summary = None
            if self.gpu_miner:
                gpu_summary = self.gpu_miner.get_summary()
            if gpu_summary is None:
                api_port = DEFAULT_API_PORT
                if self.config_builder:
                    api_port = int(
                        self.config_builder.build_gpu_config().get(
                            "api_port", DEFAULT_API_PORT
                        )
                    )
                gpu_summary = fetch_lolminer_http_summary(api_port)
            if gpu_summary:
                combined["gpu"] = self._extract_gpu_stats(gpu_summary)

            if self._shared_stats is not None:
                self._shared_stats.update(combined)

            self._push_to_comfy_clients(combined)

    def _push_to_comfy_clients(self, stats):
        clients = self._shared_clients
        if not clients:
            return

        payload = json.dumps({"type": "stats", "data": stats})
        dead = set()

        loop = self._get_event_loop() if self._get_event_loop else None
        if not loop or not loop.is_running():
            return

        for ws in clients.copy():
            try:
                asyncio.run_coroutine_threadsafe(ws.send_str(payload), loop)
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
            "type": "cpu",
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

    @staticmethod
    def _extract_gpu_stats(summary: dict) -> dict:
        """Parse T-Rex or lolMiner API response into a unified dict."""
        miner_type = summary.get("_miner_type", "lolminer")

        if miner_type == "trex":
            return DashboardServer._parse_trex(summary)
        return DashboardServer._parse_lolminer(summary)

    @staticmethod
    def _parse_trex(s: dict) -> dict:
        gpu_list = []
        for g in s.get("gpus", []):
            gpu_list.append({
                "name": g.get("name", "unknown"),
                "hashrate": g.get("hashrate", 0) / 1e6,
                "temp": g.get("temperature", 0),
                "mem_temp": g.get("memory_temperature", 0),
                "fan": g.get("fan_speed", 0),
                "power": g.get("power", 0),
            })

        total_hr = s.get("hashrate", 0) / 1e6
        pool_url = s.get("active_pool", {}).get("url", "")

        return {
            "type": "gpu",
            "software": f"T-Rex {s.get('version', '')}",
            "algo": s.get("algorithm", ""),
            "pool": pool_url,
            "uptime": s.get("uptime", 0),
            "total_hashrate": total_hr,
            "hashrate_unit": "Mh/s",
            "accepted": s.get("accepted_count", 0),
            "rejected": s.get("rejected_count", 0),
            "stales": s.get("stale_count", 0),
            "gpus": gpu_list,
        }

    @staticmethod
    def _parse_lolminer(s: dict) -> dict:
        algos = s.get("Algorithms", [{}])
        algo_info = algos[0] if algos else {}
        workers = s.get("Workers", [])
        session = s.get("Session", {})

        gpu_list = []
        for w in workers:
            gpu_list.append({
                "name": w.get("Name", "unknown"),
                "hashrate": 0,
                "temp": w.get("Core_Temp", 0),
                "mem_temp": w.get("Mem_Temp", 0),
                "fan": w.get("Fan_Speed", 0),
                "power": w.get("Power", 0),
            })

        for i, perf in enumerate(algo_info.get("Worker_Performance", [])):
            if i < len(gpu_list):
                gpu_list[i]["hashrate"] = perf

        return {
            "type": "gpu",
            "software": s.get("Software", ""),
            "algo": algo_info.get("Algorithm", ""),
            "pool": algo_info.get("Pool", ""),
            "uptime": session.get("Uptime", 0),
            "total_hashrate": algo_info.get("Total_Performance", 0),
            "hashrate_unit": algo_info.get("Performance_Unit", ""),
            "accepted": algo_info.get("Total_Accepted", 0),
            "rejected": algo_info.get("Total_Rejected", 0),
            "stales": algo_info.get("Total_Stales", 0),
            "gpus": gpu_list,
        }
