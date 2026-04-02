"""
Monitors ComfyUI's job queue and throttles miners during image generation.

CPU miner (XMRig):    max-threads-hint reduced to THROTTLE_CPU_HINT via HTTP API.
GPU miner (lolMiner): stopped entirely — ComfyUI needs full GPU access for inference.

After the queue drains, a grace period prevents rapid on/off cycling when
multiple jobs are queued back-to-back.
"""

import json
import logging
import threading
import time
import urllib.request

logger = logging.getLogger("comfyui_enhanced")

POLL_INTERVAL = 3
THROTTLE_CPU_HINT = 15
RESTORE_GRACE = 5


class JobThrottler:
    def __init__(self, cpu_miner, gpu_miner, config_builder, comfyui_port=8188):
        self._cpu = cpu_miner
        self._gpu = gpu_miner
        self._cb = config_builder
        self._url = f"http://127.0.0.1:{comfyui_port}"

        self._throttled = False
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
        logger.info("Job throttler active — polling %s/queue", self._url)

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------

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

    def _throttle(self):
        if self._throttled:
            return

        self._saved_hint = self._cb.settings.get("max_threads_hint", 50)
        self._gpu_was_alive = self._gpu is not None and self._gpu.is_alive()
        self._throttled = True
        self._idle_since = None

        logger.info(
            "ComfyUI job active — throttling (CPU %d%% -> %d%%)",
            self._saved_hint, THROTTLE_CPU_HINT,
        )
        self._cpu.set_threads_hint(THROTTLE_CPU_HINT)

        if self._gpu_was_alive:
            self._gpu.stop()
            logger.info("GPU miner paused for generation")

    def _restore(self):
        if not self._throttled:
            return

        hint = self._saved_hint or self._cb.settings.get("max_threads_hint", 50)
        self._throttled = False
        self._idle_since = None

        logger.info("ComfyUI queue empty — restoring (CPU -> %d%%)", hint)
        self._cpu.set_threads_hint(hint)

        if self._gpu_was_alive and self._gpu:
            try:
                self._gpu.start()
                logger.info("GPU miner restarted")
            except Exception as exc:
                logger.error("GPU restart failed: %s", exc)

    # ------------------------------------------------------------------

    def _run(self):
        time.sleep(15)

        while self._running:
            try:
                busy = self._queue_busy()

                if busy:
                    self._idle_since = None
                    self._throttle()
                elif self._throttled:
                    now = time.time()
                    if self._idle_since is None:
                        self._idle_since = now
                    elif now - self._idle_since >= RESTORE_GRACE:
                        self._restore()
            except Exception as exc:
                logger.error("Throttler error: %s", exc)

            time.sleep(POLL_INTERVAL)
