"""
WebSocket dashboard server.

Polls XMRig HTTP API for stats and broadcasts to connected clients.
Accepts control commands: pause, resume, set_threads, update_pool.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Set

logger = logging.getLogger("comfyui_enhanced")

try:
    import websockets
    import websockets.server
except ImportError:
    websockets = None

DEFAULT_WS_PORT = 44881
POLL_INTERVAL = 5


class DashboardServer:
    def __init__(self, miner_mgr, config_builder=None, ws_port: int = DEFAULT_WS_PORT):
        self.miner = miner_mgr
        self.config_builder = config_builder
        self.ws_port = ws_port
        self._clients: Set = set()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latest_stats: dict = {}
        self._running = False

    def start(self) -> None:
        if websockets is None:
            logger.error("websockets package not installed -- dashboard disabled")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="dashboard-ws")
        self._thread.start()
        logger.info("Dashboard server starting on port %d", self.ws_port)

    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        async with websockets.server.serve(
            self._handler,
            "0.0.0.0",
            self.ws_port,
            ping_interval=20,
            ping_timeout=60,
        ):
            poller = asyncio.create_task(self._poll_loop())
            while self._running:
                await asyncio.sleep(1)
            poller.cancel()

    async def _handler(self, ws) -> None:
        self._clients.add(ws)
        remote = ws.remote_address
        logger.info("Dashboard client connected: %s", remote)
        try:
            if self._latest_stats:
                await ws.send(json.dumps({"type": "stats", "data": self._latest_stats}))
            async for raw in ws:
                await self._handle_command(ws, raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            logger.info("Dashboard client disconnected: %s", remote)

    async def _handle_command(self, ws, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send(json.dumps({"type": "error", "msg": "invalid json"}))
            return

        cmd = msg.get("cmd")
        resp = {"type": "cmd_result", "cmd": cmd, "ok": False}

        if cmd == "pause":
            resp["ok"] = self.miner.pause()

        elif cmd == "resume":
            resp["ok"] = self.miner.resume()

        elif cmd == "set_threads":
            hint = msg.get("value", 50)
            if self.config_builder:
                cfg = self.config_builder.update_hint(int(hint))
                self.miner.write_config(cfg)
                self.miner.stop()
                self.miner.start()
                resp["ok"] = True
                resp["new_hint"] = int(hint)

        elif cmd == "update_pool":
            host = msg.get("host")
            port = msg.get("port", 443)
            user = msg.get("user")
            password = msg.get("pass")
            if host and self.config_builder:
                cfg = self.config_builder.update_pool(host, int(port), user, password)
                self.miner.write_config(cfg)
                self.miner.stop()
                self.miner.start()
                resp["ok"] = True

        elif cmd == "status":
            resp["ok"] = True
            resp["alive"] = self.miner.is_alive()
            resp["stats"] = self._latest_stats

        else:
            resp["msg"] = f"unknown command: {cmd}"

        await ws.send(json.dumps(resp))

    async def _poll_loop(self) -> None:
        while self._running:
            summary = self.miner.get_summary()
            if summary:
                self._latest_stats = self._extract_stats(summary)
                payload = json.dumps({"type": "stats", "data": self._latest_stats})
                dead = set()
                for client in self._clients.copy():
                    try:
                        await client.send(payload)
                    except Exception:
                        dead.add(client)
                self._clients -= dead
            await asyncio.sleep(POLL_INTERVAL)

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
