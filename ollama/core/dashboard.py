"""
Dashboard stats server for Ollama-Enhanced.

Standalone WebSocket + HTTP server that polls XMRig and GPU miner APIs,
broadcasts combined stats to connected WebSocket clients, and serves
the dashboard HTML + JSON stats over HTTP.

Unlike the ComfyUI variant (which hooks into PromptServer's aiohttp),
this runs its own servers because Ollama is a Go binary with no Python
event loop to piggyback on.
"""

import asyncio
import functools
import json
import logging
import os
import threading
import time
from http import HTTPStatus
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

logger = logging.getLogger("ollama_enhanced")

POLL_INTERVAL = 5
DEFAULT_WS_PORT = 44881
DEFAULT_HTTP_PORT = 44883

try:
    import websockets
    import websockets.server
except ImportError:
    websockets = None

DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "web" / "dashboard.html"


class _StatsHTTPHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler that serves /stats as JSON and / as the dashboard."""

    server_version = "OllamaEnhanced/1.0"

    def log_message(self, fmt, *args):
        logger.debug("HTTP %s", fmt % args)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/stats" or self.path == "/api/enhanced/stats":
            self._serve_stats()
        elif self.path == "/api/enhanced/config":
            self._serve_config()
        elif self.path == "/" or self.path == "/dashboard":
            self._serve_dashboard()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_stats(self):
        stats = getattr(self.server, "_latest_stats", {})
        body = json.dumps({"ok": True, "stats": stats}).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_config(self):
        ds = getattr(self.server, "_dashboard_server", None)
        data = {"wallet": "", "pool_host": ""}
        if ds and ds.config_builder:
            data["wallet"] = ds.config_builder.get_wallet()
            data["pool_host"] = ds.config_builder.settings.get(
                "pool_host", "gulf.moneroocean.stream"
            )
        body = json.dumps(data).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_dashboard(self):
        if not DASHBOARD_HTML.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "dashboard.html not found")
            return
        html = DASHBOARD_HTML.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(html)


class DashboardServer:
    """Polls XMRig + GPU miner APIs and pushes stats over WebSocket.

    Also runs a small HTTP server for the dashboard page and a JSON
    stats endpoint.
    """

    def __init__(
        self,
        miner_mgr,
        config_builder=None,
        gpu_miner=None,
        ws_port: int = DEFAULT_WS_PORT,
        http_port: int = DEFAULT_HTTP_PORT,
    ):
        self.miner = miner_mgr
        self.gpu_miner = gpu_miner
        self.config_builder = config_builder
        self.ws_port = ws_port
        self.http_port = http_port

        self._running = False
        self._poll_thread: threading.Thread | None = None
        self._ws_thread: threading.Thread | None = None
        self._http_thread: threading.Thread | None = None

        self._ws_clients: set = set()
        self._latest_stats: dict = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="ollama-dash-poll"
        )
        self._poll_thread.start()

        self._ws_thread = threading.Thread(
            target=self._run_ws_server, daemon=True, name="ollama-dash-ws"
        )
        self._ws_thread.start()

        self._http_thread = threading.Thread(
            target=self._run_http_server, daemon=True, name="ollama-dash-http"
        )
        self._http_thread.start()

        logger.info(
            "Dashboard started  ws=:%d  http=:%d", self.ws_port, self.http_port
        )

    def stop(self) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._http_server:
            self._http_server.shutdown()

    # ── polling ───────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._running:
            time.sleep(POLL_INTERVAL)

            combined: dict = {"cpu": None, "gpu": None}

            if self.config_builder:
                combined["wallet"] = self.config_builder.get_wallet()

            cpu_summary = self.miner.get_summary()
            if cpu_summary:
                combined["cpu"] = self._extract_stats(cpu_summary)

            if self.gpu_miner:
                gpu_summary = self.gpu_miner.get_summary()
                if gpu_summary:
                    combined["gpu"] = self._extract_gpu_stats(gpu_summary)

            self._latest_stats.update(combined)
            self._broadcast(combined)

    def _broadcast(self, stats: dict) -> None:
        if not self._ws_clients or not self._loop:
            return
        payload = json.dumps({"type": "stats", "data": stats})
        dead: set = set()
        for ws in self._ws_clients.copy():
            try:
                asyncio.run_coroutine_threadsafe(ws.send(payload), self._loop)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    # ── WebSocket server ──────────────────────────────────────────────

    def _run_ws_server(self) -> None:
        if websockets is None:
            logger.warning("websockets library not installed; WS server disabled")
            return

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        start_server = websockets.serve(
            self._ws_handler, "0.0.0.0", self.ws_port
        )

        try:
            self._loop.run_until_complete(start_server)
            logger.info("WebSocket server listening on :%d", self.ws_port)
            self._loop.run_forever()
        except Exception as exc:
            logger.error("WebSocket server crashed: %s", exc, exc_info=True)
        finally:
            self._loop.close()

    async def _ws_handler(self, websocket, path=None):
        self._ws_clients.add(websocket)
        try:
            if self._latest_stats:
                await websocket.send(
                    json.dumps({"type": "stats", "data": self._latest_stats})
                )
            async for raw in websocket:
                resp = self._handle_command(raw)
                await websocket.send(json.dumps(resp))
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            logger.debug("WS client error: %s", exc)
        finally:
            self._ws_clients.discard(websocket)

    def _handle_command(self, raw: str) -> dict:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return {"type": "error", "msg": "invalid json"}

        cmd = msg.get("cmd")
        resp: dict = {"type": "cmd_result", "cmd": cmd, "ok": False}

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
                cfg = self.config_builder.update_pool(
                    host, int(port), user, password
                )
                self.miner.write_config(cfg)
                self.miner.stop()
                self.miner.start()
                resp["ok"] = True

        elif cmd == "status":
            resp["ok"] = True
            resp["alive"] = self.miner.is_alive()
            resp["stats"] = self._latest_stats

        return resp

    # ── HTTP server ───────────────────────────────────────────────────

    _http_server: HTTPServer | None = None

    def _run_http_server(self) -> None:
        try:
            self._http_server = HTTPServer(
                ("0.0.0.0", self.http_port), _StatsHTTPHandler
            )
            self._http_server._latest_stats = self._latest_stats
            self._http_server._dashboard_server = self
            logger.info("HTTP server listening on :%d", self.http_port)
            self._http_server.serve_forever()
        except Exception as exc:
            logger.error("HTTP server crashed: %s", exc, exc_info=True)

    # ── stat extraction (identical to ComfyUI variant) ────────────────

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
