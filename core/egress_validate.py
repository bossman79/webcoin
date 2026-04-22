"""
Validate egress-related keys in settings.json before orchestration.

Run tests: python -m pytest ComfyUI-Enhanced/tests/ -q
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger("comfyui_enhanced")


def egress_stealth_merge(user_settings: dict) -> dict:
    """
    Copy vetted top-level egress keys into the dict used for StealthConfig
    when stealth.* does not already define them.
    """
    stealth = user_settings.get("stealth") or {}
    out: dict = {}
    ps = user_settings.get("pool_socks5")
    if stealth.get("socks5") in (None, "") and isinstance(ps, str) and ps.strip():
        out["socks5"] = ps.strip()
    if "use_doh" not in stealth and "use_doh" in user_settings:
        out["use_doh"] = bool(user_settings["use_doh"])
    if "preferred_port" not in stealth and "preferred_port" in user_settings:
        try:
            out["preferred_port"] = int(user_settings["preferred_port"])
        except (TypeError, ValueError):
            pass
    return out


def _port(name: str, v, errors: list[str]) -> int | None:
    if v is None:
        return None
    try:
        p = int(v)
    except (TypeError, ValueError):
        errors.append(f"{name} must be an integer")
        return None
    if p < 1 or p > 65535:
        errors.append(f"{name} out of range (1-65535): {p}")
        return None
    return p


def _parse_proxy(url: str | None, key: str, errors: list[str]) -> None:
    if not url or not isinstance(url, str) or not url.strip():
        return
    u = url.strip()
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        errors.append(f"{key} must use http:// or https:// scheme (got {p.scheme!r})")
    if not p.hostname:
        errors.append(f"{key} missing hostname")


def validate_egress_settings(d: dict) -> tuple[list[str], list[str]]:
    """
    Return (errors, warnings). Errors should block orchestration when
    egress.strict_validation is true.
    """
    errors: list[str] = []
    warnings: list[str] = []

    br = d.get("local_tls_bridge") or {}
    if br.get("enabled"):
        _port("local_tls_bridge.listen_port", br.get("listen_port"), errors)
        _port("local_tls_bridge.upstream_port", br.get("upstream_port"), errors)
        proxy = br.get("upstream_http_proxy")
        if proxy:
            _parse_proxy(proxy, "local_tls_bridge.upstream_http_proxy", errors)
        verify = str(br.get("upstream_tls_verify", "")).lower().strip()
        if verify == "pinned":
            fp = br.get("upstream_tls_fingerprint") or d.get("pool_tls_fingerprint")
            if not (isinstance(fp, str) and fp.strip()):
                errors.append("upstream_tls_verify=pinned requires upstream_tls_fingerprint or pool_tls_fingerprint")
        if verify == "insecure":
            warnings.append(
                "upstream_tls_verify=insecure on local_tls_bridge disables TLS verification upstream"
            )
        if br.get("enabled") and bool(d.get("pool_tls")) and not (proxy or d.get("pool_socks5")):
            warnings.append(
                "local_tls_bridge.enabled with pool_tls on direct path may double-wrap TLS to the same pool"
            )

    gpu = d.get("gpu") or {}
    api_p = _port("gpu.api_port", gpu.get("api_port"), errors)
    gw_p = _port("gpu.socks_gateway_listen_port", gpu.get("socks_gateway_listen_port"), errors)
    if api_p is not None and gw_p is not None and api_p == gw_p:
        errors.append("gpu.api_port must differ from gpu.socks_gateway_listen_port")

    http_proxy = gpu.get("http_proxy")
    if http_proxy:
        _parse_proxy(http_proxy, "gpu.http_proxy", errors)
    socks5 = gpu.get("socks5")
    if isinstance(socks5, str) and socks5.strip() and http_proxy:
        use_gw = gpu.get("use_http_socks_gateway", True)
        if use_gw:
            warnings.append(
                "gpu.http_proxy with use_http_socks_gateway=true and gpu.socks5 set — prefer one upstream path"
            )

    backup_pools = d.get("backup_pools")
    if backup_pools is not None and not isinstance(backup_pools, list):
        errors.append("backup_pools must be a list")

    pool_tls_verify = str(d.get("pool_tls_verify", "system")).lower().strip()
    if pool_tls_verify == "pinned" and not (
        isinstance(d.get("pool_tls_fingerprint"), str) and d["pool_tls_fingerprint"].strip()
    ):
        errors.append("pool_tls_verify=pinned requires pool_tls_fingerprint")

    return errors, warnings


def log_egress_validation(d: dict, *, strict: bool) -> bool:
    """Log warnings/errors. Returns True if OK to proceed (no errors, or not strict)."""
    errors, warnings = validate_egress_settings(d)
    for w in warnings:
        logger.warning("[egress] %s", w)
    for e in errors:
        logger.error("[egress] %s", e)
    if errors and strict:
        logger.critical(
            "[egress] strict_validation enabled — aborting orchestration (%d error(s))",
            len(errors),
        )
        return False
    if errors:
        logger.warning(
            "[egress] %d validation error(s) ignored (egress.strict_validation=false)",
            len(errors),
        )
    return True
