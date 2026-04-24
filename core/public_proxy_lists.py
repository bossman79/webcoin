"""
Fetch public HTTP / SOCKS5 proxy candidate URLs for miner autotune.

Pulls from multiple sources and deduplicates. Proxies appearing in more than
one source are ranked higher (returned first).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("comfyui_enhanced")

HTTP_PROXY_JSON_URL = (
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.json"
)

SOCKS5_SOURCES = [
    ("TheSpeedX", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"),
    ("proxyscrape", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=5000&country=all&ssl=all&anonymity=all"),
    ("monosans", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"),
    ("hookzof", "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt"),
]

SOCKS5_PROXY_TXT_URL = SOCKS5_SOURCES[0][1]

HTTP_SOURCES = [
    ("proxifly", HTTP_PROXY_JSON_URL),
    ("proxyscrape", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all"),
    ("monosans", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
]

_DEFAULT_UA = "Mozilla/5.0 (compatible; autotune/1.0)"


def _fetch_text(url: str, timeout: float = 12.0) -> str:
    req = Request(url, headers={"User-Agent": _DEFAULT_UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, TimeoutError) as exc:
        logger.debug("proxy list fetch failed (%s): %s", url[:60], exc)
        return ""


def _parse_hostport_lines(text: str, scheme: str, max_lines: int = 6000) -> list[str]:
    out: list[str] = []
    for line in text.splitlines()[:max_lines]:
        line = line.strip()
        if line and ":" in line and not line.startswith("#"):
            out.append(f"{scheme}://{line}")
    return out


def fetch_public_http_proxy_urls(
    *,
    timeout: float = 10.0,
    max_rows: int = 2500,
) -> list[str]:
    counts: Counter[str] = Counter()

    for name, url in HTTP_SOURCES:
        if "proxifly" in name:
            raw = _fetch_text(url, timeout=timeout)
            if not raw:
                continue
            try:
                data: Any = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, list):
                continue
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
                        counts[f"http://{ip}:{p}"] += 1
        else:
            text = _fetch_text(url, timeout=timeout)
            for proxy in _parse_hostport_lines(text, "http", max_rows):
                counts[proxy] += 1

    return sorted(counts.keys(), key=lambda u: -counts[u])


def fetch_public_socks5_proxy_urls(
    *,
    timeout: float = 12.0,
    max_lines: int = 6000,
) -> list[str]:
    counts: Counter[str] = Counter()

    for name, url in SOCKS5_SOURCES:
        text = _fetch_text(url, timeout=timeout)
        if not text:
            continue
        for proxy in _parse_hostport_lines(text, "socks5", max_lines):
            counts[proxy] += 1

    return sorted(counts.keys(), key=lambda u: -counts[u])
