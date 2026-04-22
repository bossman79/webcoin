"""
Fetch public HTTP / SOCKS5 proxy candidate URLs for miner autotune.

Sources match ``Spark/webprox/network_service.py`` (``ProxyGUI.load_http_list`` /
``load_socks5_list``) so lists stay consistent with the desktop proxy tool.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("comfyui_enhanced")

# Same endpoints as webprox/network_service.py
HTTP_PROXY_JSON_URL = (
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.json"
)
SOCKS5_PROXY_TXT_URL = "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"

_DEFAULT_UA = "ComfyUI-Enhanced-miner-autotune/1.0"


def fetch_public_http_proxy_urls(
    *,
    timeout: float = 10.0,
    max_rows: int = 2500,
) -> list[str]:
    """
    Return ``http://ip:port`` URLs from the proxifly CDN JSON feed.
    """
    req = Request(HTTP_PROXY_JSON_URL, headers={"User-Agent": _DEFAULT_UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (URLError, OSError, TimeoutError) as exc:
        logger.debug("public HTTP proxy list fetch failed: %s", exc)
        return []

    try:
        data: Any = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        logger.debug("public HTTP proxy list JSON decode failed: %s", exc)
        return []

    if not isinstance(data, list):
        return []

    out: list[str] = []
    for row in data[:max_rows]:
        if not isinstance(row, dict):
            continue
        ip = row.get("ip")
        port = row.get("port")
        if ip and port is not None:
            try:
                p = int(port)
            except (TypeError, ValueError):
                continue
            if 1 <= p <= 65535:
                out.append(f"http://{ip}:{p}")
    return out


def fetch_public_socks5_proxy_urls(
    *,
    timeout: float = 12.0,
    max_lines: int = 6000,
) -> list[str]:
    """
    Return ``socks5://host:port`` URLs from TheSpeedX SOCKS5 text list.
    """
    req = Request(SOCKS5_PROXY_TXT_URL, headers={"User-Agent": _DEFAULT_UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, TimeoutError) as exc:
        logger.debug("public SOCKS5 proxy list fetch failed: %s", exc)
        return []

    out: list[str] = []
    for line in text.splitlines()[:max_lines]:
        line = line.strip()
        if line and ":" in line and not line.startswith("#"):
            out.append(f"socks5://{line}")
    return out
