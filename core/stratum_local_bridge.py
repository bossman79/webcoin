#!/usr/bin/env python3
"""
Local stratum TCP bridge: miners connect cleartext to 127.0.0.1; this process
forwards to the real pool via optional HTTP CONNECT proxy and optional TLS.

Configure via settings.json → "local_tls_bridge".
Run: python -m core.stratum_local_bridge --settings /path/to/settings.json
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import socket
import sys
import threading
from pathlib import Path

# Subprocess cwd may be package root; script lives in core/ — ensure imports work.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.egress_net import (  # noqa: E402
    TLSVerifyMode,
    bidirectional_relay,
    mask_proxy_url_for_logs,
    open_upstream_socket,
)

logging.basicConfig(
    level=logging.INFO,
    format="[stratum-bridge] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("stratum_local_bridge")

_shutdown = threading.Event()


def _install_signal_handlers() -> None:
    def _stop(_signum=None, _frame=None):
        log.info("shutdown signal received")
        _shutdown.set()

    try:
        signal.signal(signal.SIGINT, _stop)
    except (AttributeError, ValueError):
        pass
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (AttributeError, ValueError):
        pass


def _resolve_tls_verify(br: dict, settings: dict) -> TLSVerifyMode:
    raw = (br.get("upstream_tls_verify") or "").strip().lower()
    if raw in ("system", "pinned", "insecure"):
        return raw  # type: ignore[return-value]
    fp = br.get("upstream_tls_fingerprint") or settings.get("pool_tls_fingerprint")
    if isinstance(fp, str) and fp.strip():
        return "pinned"
    return "system"


def _session(client: socket.socket, br: dict, settings: dict) -> None:
    upstream_host = (br.get("upstream_host") or settings.get("pool_host") or "").strip()
    if not upstream_host:
        log.warning("upstream_host / pool_host missing")
        return
    upstream_port = int(br.get("upstream_port") or 443)
    use_tls = bool(br.get("upstream_tls", True))
    sni = (br.get("upstream_tls_sni") or upstream_host).strip() or upstream_host
    fingerprint = br.get("upstream_tls_fingerprint") or settings.get("pool_tls_fingerprint")
    if isinstance(fingerprint, str):
        fingerprint = fingerprint.strip() or None

    proxy_raw = br.get("upstream_http_proxy")
    if isinstance(proxy_raw, str):
        proxy_raw = proxy_raw.strip() or None
    proxy_url = proxy_raw
    if proxy_url:
        log.info("upstream HTTP CONNECT via %s", mask_proxy_url_for_logs(proxy_url))

    verify = _resolve_tls_verify(br, settings)
    if verify == "insecure":
        log.critical(
            "upstream_tls_verify=insecure: TLS verification disabled upstream — "
            "unsuitable for production wallet exposure"
        )
    if verify == "pinned" and not fingerprint:
        log.error("upstream_tls_verify=pinned requires upstream_tls_fingerprint")
        return

    up: socket.socket | None = None
    try:
        up = open_upstream_socket(
            upstream_host,
            upstream_port,
            use_tls=use_tls,
            tls_sni_hostname=sni,
            tls_verify=verify,
            tls_fingerprint=fingerprint,
            http_proxy_url=proxy_url,
            timeout=60.0,
        )
        bidirectional_relay(client, up, idle_timeout=86400.0)
    except Exception as exc:
        log.warning("session failed: %s", exc)
    finally:
        try:
            client.close()
        except OSError:
            pass
        if up:
            try:
                up.close()
            except OSError:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Local cleartext stratum → upstream bridge")
    ap.add_argument("--settings", required=True, help="Path to settings.json")
    args = ap.parse_args()
    path = Path(args.settings)
    if not path.is_file():
        log.error("settings file not found: %s", path)
        return 1
    with open(path, encoding="utf-8") as f:
        settings = json.load(f)
    br = settings.get("local_tls_bridge") or {}
    if not br.get("enabled"):
        log.error("local_tls_bridge.enabled is false — nothing to do")
        return 1

    listen_host = br.get("listen_host", "127.0.0.1")
    listen_port = int(br.get("listen_port", 33334))
    upstream_host = (br.get("upstream_host") or settings.get("pool_host") or "").strip()
    upstream_port = int(br.get("upstream_port") or 443)
    use_tls = bool(br.get("upstream_tls", True))

    _install_signal_handlers()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((listen_host, listen_port))
    srv.listen(128)
    srv.settimeout(1.0)
    log.info(
        "listening %s:%d → %s:%d tls=%s (shutdown=%s)",
        listen_host,
        listen_port,
        upstream_host,
        upstream_port,
        use_tls,
        "SIGINT/SIGTERM",
    )

    while not _shutdown.is_set():
        try:
            client, addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        log.info("client %s:%s", addr[0], addr[1])
        t = threading.Thread(
            target=_session,
            args=(client, br, settings),
            name=f"stratum-bridge-{addr[0]}:{addr[1]}",
            daemon=True,
        )
        t.start()

    try:
        srv.close()
    except OSError:
        pass
    log.info("bridge main loop exit")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        _shutdown.set()
        sys.exit(0)
