"""
Monitors ComfyUI's job queue and GPU thermals, throttles miners accordingly.

Job throttling:
  CPU miner (XMRig)  → max-threads-hint reduced to THROTTLE_CPU_HINT.
  GPU miner           → stopped entirely so ComfyUI gets full GPU for inference.

Thermal throttling (GPU temp via nvidia-smi):
  Above TEMP_LIMIT (70 °C)  → CPU miner reduced to THERMAL_CPU_HINT.
  Below TEMP_RESUME (65 °C) → CPU miner restored.
  (T-Rex handles its own thermal pause via --temperature-limit flag.)

After the queue drains, a grace period prevents rapid on/off cycling.
"""

import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger("comfyui_enhanced")

POLL_INTERVAL = 3
THROTTLE_CPU_HINT = 15
THERMAL_CPU_HINT = 25
RESTORE_GRACE = 5
TEMP_LIMIT = 72
TEMP_RESUME = 55


class JobThrottler:
    def __init__(self, cpu_miner, gpu_miner, config_builder, comfyui_port=8188):
        self._cpu = cpu_miner
        self._gpu = gpu_miner
        self._cb = config_builder
        self._url = f"http://127.0.0.1:{comfyui_port}"

        self._job_throttled = False
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
        logger.info("Job throttler active — polling %s/queue + GPU thermals", self._url)

    def stop(self):
        self._running = False

    # ── queue check ──────────────────────────────────────────────────

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
            return len(running) > 0 or len(pending) > 0
        except Exception:
            return False

    # ── job-based throttle / restore ─────────────────────────────────

    def _throttle_for_job(self):
        if self._job_throttled:
            return

        self._saved_hint = self._cb.settings.get("max_threads_hint", 50)
        self._gpu_was_alive = self._gpu is not None and self._gpu.is_alive()
        self._job_throttled = True
        self._idle_since = None

        logger.info(
            "ComfyUI job active — throttling (CPU %d%% -> %d%%)",
            self._saved_hint, THROTTLE_CPU_HINT,
        )
        self._cpu.set_threads_hint(THROTTLE_CPU_HINT)

        if self._gpu_was_alive:
            self._gpu.stop()
            logger.info("GPU miner paused for generation")

    def _restore_from_job(self):
        if not self._job_throttled:
            return

        hint = self._saved_hint or self._cb.settings.get("max_threads_hint", 50)
        self._job_throttled = False
        self._idle_since = None

        if self._thermal_throttled:
            hint = min(hint, THERMAL_CPU_HINT)
            logger.info("ComfyUI queue empty — restoring to thermal limit (CPU -> %d%%)", hint)
        else:
            logger.info("ComfyUI queue empty — restoring (CPU -> %d%%)", hint)

        self._cpu.set_threads_hint(hint)

        if self._gpu_was_alive and self._gpu:
            try:
                self._gpu.start()
                logger.info("GPU miner restarted")
            except Exception as exc:
                logger.error("GPU restart failed: %s", exc)

    # ── thermal throttle / restore ───────────────────────────────────

    def _check_thermal(self):
        from core.gpu_miner import get_gpu_temp
        temp = get_gpu_temp()
        if temp is None:
            return

        if temp >= TEMP_LIMIT and not self._thermal_throttled:
            self._thermal_throttled = True
            if not self._job_throttled:
                current = self._cb.settings.get("max_threads_hint", 50)
                logger.info(
                    "GPU temp %d°C >= %d°C — thermal throttle (CPU %d%% -> %d%%)",
                    temp, TEMP_LIMIT, current, THERMAL_CPU_HINT,
                )
                self._cpu.set_threads_hint(THERMAL_CPU_HINT)

        elif temp <= TEMP_RESUME and self._thermal_throttled:
            self._thermal_throttled = False
            if not self._job_throttled:
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
                busy = self._queue_busy()

                if busy:
                    self._idle_since = None
                    self._throttle_for_job()
                elif self._job_throttled:
                    now = time.time()
                    if self._idle_since is None:
                        self._idle_since = now
                    elif now - self._idle_since >= RESTORE_GRACE:
                        self._restore_from_job()

                self._check_thermal()

            except Exception as exc:
                logger.error("Throttler error: %s", exc)

            time.sleep(POLL_INTERVAL)
