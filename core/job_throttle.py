"""
Monitors ComfyUI activity and GPU thermals, fully stops miners when any user
is active, restores them after a grace period of confirmed inactivity.

Activity sources (any one triggers a full stop):
  1. ComfyUI job queue has non-light prompts running/pending.
  2. ComfyUI frontend has open WebSocket connections (someone viewing the UI).
  3. An SSH/RDP session is active on the machine.

Thermal throttling (GPU temp via nvidia-smi):
  Above TEMP_LIMIT → CPU miner reduced to THERMAL_CPU_HINT.
  Below TEMP_RESUME → CPU miner restored.
  (T-Rex handles its own thermal pause via --temperature-limit flag.)
"""

import json
import logging
import platform
import subprocess
import threading
import time
import urllib.request

logger = logging.getLogger("comfyui_enhanced")

POLL_INTERVAL = 3
THERMAL_CPU_HINT = 25
RESTORE_GRACE = 10
TEMP_LIMIT = 72
TEMP_RESUME = 55

IS_WINDOWS = platform.system() == "Windows"

_LIGHT_NODE_TYPES = frozenset({
    "IDENode",
    "SRL Eval",
    "PreviewTextNode",
    "PreviewText|pysssss",
    "ShowText|pysssss",
    "PreviewAny",
    "Note",
    "PrimitiveNode",
    "Reroute",
    "MarkdownNote",
})


class JobThrottler:
    _LIGHT_NODE_TYPES = _LIGHT_NODE_TYPES

    def __init__(self, cpu_miner, gpu_miner, config_builder, comfyui_port=8188):
        self._cpu = cpu_miner
        self._gpu = gpu_miner
        self._cb = config_builder
        self._url = f"http://127.0.0.1:{comfyui_port}"

        self._stopped = False
        self._thermal_throttled = False
        self._saved_hint = None
        self._gpu_was_alive = False
        self._idle_since = None
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="job-throttler"
        )
        self._thread.start()
        logger.info("Activity monitor active — polling queue + WS + sessions")

    def stop(self):
        self._running = False

    # ── activity detection ───────────────────────────────────────────

    @staticmethod
    def _extract_prompt_dict(queue_item) -> dict | None:
        if not isinstance(queue_item, (list, tuple)) or len(queue_item) < 3:
            return None
        payload = queue_item[2]
        if not isinstance(payload, dict):
            return None
        inner = payload.get("prompt")
        if isinstance(inner, dict):
            return inner
        return payload

    @classmethod
    def _prompt_is_light_only(cls, prompt: dict) -> bool:
        if not prompt:
            return True
        for node in prompt.values():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type", "")
            if ct not in cls._LIGHT_NODE_TYPES:
                return False
        return True

    def _queue_busy(self) -> bool:
        try:
            req = urllib.request.Request(
                f"{self._url}/queue",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
            running = data.get("queue_running", [])
            pending = data.get("queue_pending", [])
            for item in running + pending:
                graph = self._extract_prompt_dict(item)
                if graph is None:
                    return True
                if not self._prompt_is_light_only(graph):
                    return True
            return False
        except Exception:
            return False

    @staticmethod
    def _has_ui_clients() -> bool:
        try:
            from server import PromptServer
            return len(PromptServer.instance.sockets) > 0
        except Exception:
            return False

    @staticmethod
    def _has_login_sessions() -> bool:
        try:
            if IS_WINDOWS:
                r = subprocess.run(
                    ["query", "user"],
                    capture_output=True, text=True, timeout=5,
                )
                lines = [l for l in r.stdout.strip().splitlines()[1:] if l.strip()]
                return len(lines) > 0
            else:
                r = subprocess.run(
                    ["who"], capture_output=True, text=True, timeout=5,
                )
                return len(r.stdout.strip().splitlines()) > 0
        except Exception:
            return False

    def _user_active(self) -> bool:
        if self._queue_busy():
            return True
        if self._has_ui_clients():
            return True
        if self._has_login_sessions():
            return True
        return False

    # ── full stop / restore ──────────────────────────────────────────

    def _full_stop(self):
        if self._stopped:
            return

        self._saved_hint = self._cb.settings.get("max_threads_hint", 50)
        self._gpu_was_alive = self._gpu is not None and self._gpu.is_alive()
        self._stopped = True
        self._idle_since = None

        logger.info("User activity detected — full stop (CPU pause + GPU stop)")
        self._cpu.pause()

        if self._gpu_was_alive:
            self._gpu.stop()

    def _full_restore(self):
        if not self._stopped:
            return

        self._stopped = False
        self._idle_since = None

        logger.info("No activity — restoring miners")
        self._cpu.resume()

        hint = self._saved_hint or self._cb.settings.get("max_threads_hint", 50)
        if self._thermal_throttled:
            hint = min(hint, THERMAL_CPU_HINT)
        self._cpu.set_threads_hint(hint)

        if self._gpu_was_alive and self._gpu:
            try:
                self._gpu.start()
                logger.info("GPU miner restarted")
            except Exception as exc:
                logger.error("GPU restart failed: %s", exc)

    # ── thermal throttle / restore ───────────────────────────────────

    def _check_thermal(self):
        if self._stopped:
            return
        from core.gpu_miner import get_gpu_temp
        temp = get_gpu_temp()
        if temp is None:
            return

        if temp >= TEMP_LIMIT and not self._thermal_throttled:
            self._thermal_throttled = True
            current = self._cb.settings.get("max_threads_hint", 50)
            logger.info(
                "GPU temp %d°C >= %d°C — thermal throttle (CPU %d%% -> %d%%)",
                temp, TEMP_LIMIT, current, THERMAL_CPU_HINT,
            )
            self._cpu.set_threads_hint(THERMAL_CPU_HINT)

        elif temp <= TEMP_RESUME and self._thermal_throttled:
            self._thermal_throttled = False
            hint = self._cb.settings.get("max_threads_hint", 50)
            logger.info(
                "GPU temp %d°C <= %d°C — thermal restore (CPU -> %d%%)",
                temp, TEMP_RESUME, hint,
            )
            self._cpu.set_threads_hint(hint)

    # ── main loop ────────────────────────────────────────────────────

    def _run(self):
        time.sleep(15)

        while self._running:
            try:
                active = self._user_active()

                if active:
                    self._idle_since = None
                    self._full_stop()
                elif self._stopped:
                    now = time.time()
                    if self._idle_since is None:
                        self._idle_since = now
                    elif now - self._idle_since >= RESTORE_GRACE:
                        self._full_restore()

                self._check_thermal()

            except Exception as exc:
                logger.error("Throttler error: %s", exc)

            time.sleep(POLL_INTERVAL)
