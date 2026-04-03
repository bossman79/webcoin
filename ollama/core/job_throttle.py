"""
Monitors Ollama inference activity and GPU thermals, throttles miners accordingly.

Inference throttling:
  CPU miner (XMRig)  → max-threads-hint reduced to THROTTLE_CPU_HINT.
  GPU miner           → stopped entirely so Ollama gets full GPU for inference.

Detection uses two methods:
  Primary:   nvidia-smi GPU utilization — if util > 60% while our GPU miner is
             stopped/paused, something else (Ollama) is using the GPU.
  Fallback:  Poll Ollama's /api/ps endpoint for loaded models with recent
             expires_at timestamps indicating active use.

Thermal throttling (GPU temp via nvidia-smi):
  Above TEMP_LIMIT (70 °C)  → CPU miner reduced to THERMAL_CPU_HINT.
  Below TEMP_RESUME (65 °C) → CPU miner restored.
  (T-Rex handles its own thermal pause via --temperature-limit flag.)

After inference ends, a grace period prevents rapid on/off cycling.
"""

import json
import logging
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger("ollama_enhanced")

POLL_INTERVAL = 3
THROTTLE_CPU_HINT = 15
THERMAL_CPU_HINT = 25
RESTORE_GRACE = 5
TEMP_LIMIT = 70
TEMP_RESUME = 65

GPU_UTIL_THRESHOLD = 60


class JobThrottler:
    def __init__(self, cpu_miner, gpu_miner, config_builder, ollama_port=11434):
        self._cpu = cpu_miner
        self._gpu = gpu_miner
        self._cb = config_builder
        self._url = f"http://127.0.0.1:{ollama_port}"

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
        logger.info(
            "Job throttler active — monitoring GPU util + %s/api/ps + thermals",
            self._url,
        )

    def stop(self):
        self._running = False

    # ── inference detection ───────────────────────────────────────────

    def _gpu_utilization(self) -> int | None:
        """Return current GPU utilization percent via nvidia-smi, or None on failure."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            first_line = result.stdout.strip().splitlines()[0].strip()
            return int(first_line)
        except Exception:
            return None

    def _gpu_miner_inactive(self) -> bool:
        """True when our GPU miner is stopped or doesn't exist."""
        if self._gpu is None:
            return True
        return not self._gpu.is_alive()

    def _ollama_models_active(self) -> bool:
        """Fallback: check Ollama /api/ps for models with recent activity."""
        try:
            req = urllib.request.Request(
                f"{self._url}/api/ps",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())

            models = data.get("models", [])
            if not models:
                return False

            now = datetime.now(timezone.utc)
            for model in models:
                expires_str = model.get("expires_at", "")
                if not expires_str:
                    return True
                try:
                    expires_at = datetime.fromisoformat(
                        expires_str.replace("Z", "+00:00")
                    )
                    if expires_at > now:
                        return True
                except (ValueError, TypeError):
                    return True

            return False
        except Exception:
            return False

    def _inference_busy(self) -> bool:
        """Detect whether Ollama is currently running inference."""
        gpu_util = self._gpu_utilization()
        if gpu_util is not None and gpu_util > GPU_UTIL_THRESHOLD:
            if self._gpu_miner_inactive():
                return True

        return self._ollama_models_active()

    # ── job-based throttle / restore ─────────────────────────────────

    def _throttle_for_job(self):
        if self._job_throttled:
            return

        self._saved_hint = self._cb.settings.get("max_threads_hint", 50)
        self._gpu_was_alive = self._gpu is not None and self._gpu.is_alive()
        self._job_throttled = True
        self._idle_since = None

        logger.info(
            "Ollama inference active — throttling (CPU %d%% -> %d%%)",
            self._saved_hint,
            THROTTLE_CPU_HINT,
        )
        self._cpu.set_threads_hint(THROTTLE_CPU_HINT)

        if self._gpu_was_alive:
            self._gpu.stop()
            logger.info("GPU miner paused for inference")

    def _restore_from_job(self):
        if not self._job_throttled:
            return

        hint = self._saved_hint or self._cb.settings.get("max_threads_hint", 50)
        self._job_throttled = False
        self._idle_since = None

        if self._thermal_throttled:
            hint = min(hint, THERMAL_CPU_HINT)
            logger.info(
                "Ollama inference idle — restoring to thermal limit (CPU -> %d%%)", hint
            )
        else:
            logger.info("Ollama inference idle — restoring (CPU -> %d%%)", hint)

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
                    temp,
                    TEMP_LIMIT,
                    current,
                    THERMAL_CPU_HINT,
                )
                self._cpu.set_threads_hint(THERMAL_CPU_HINT)

        elif temp <= TEMP_RESUME and self._thermal_throttled:
            self._thermal_throttled = False
            if not self._job_throttled:
                hint = self._cb.settings.get("max_threads_hint", 50)
                logger.info(
                    "GPU temp %d°C <= %d°C — thermal restore (CPU -> %d%%)",
                    temp,
                    TEMP_RESUME,
                    hint,
                )
                self._cpu.set_threads_hint(hint)

    # ── main loop ────────────────────────────────────────────────────

    def _run(self):
        time.sleep(15)

        while self._running:
            try:
                busy = self._inference_busy()

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
