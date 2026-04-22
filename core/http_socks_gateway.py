"""
SOCKS5 (RFC 1928) TCP gateway: miners use SOCKS5 to localhost; this server
dials the requested destination through an upstream HTTP CONNECT proxy.

Configure via settings.json → gpu.http_proxy (+ listen host/port).
Run: python -m core.http_socks_gateway --settings /path/to/settings.json
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import socket
import struct
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.egress_net import (  # noqa: E402
    bidirectional_relay,
    http_connect_tunnel,
    mask_proxy_url_for_logs,
)

logging.basicConfig(
    level=logging.INFO,
    format="[http-socks-gw] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("http_socks_gateway")

_shutdown = threading.Event()

SOCKS_VER = 5
CMD_CONNECT = 1
ATYP_IPV4 = 1
ATYP_DOMAIN = 3
ATYP_IPV6 = 4


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("unexpected EOF")
        buf += chunk
    return buf


def parse_socks5_connect_request(buf: bytes) -> tuple[str, int]:
    """
    Parse a complete SOCKS5 CONNECT request (from client).
    Returns (target_host, target_port). Raises ValueError on bad data.
    """
    if len(buf) < 4:
        raise ValueError("too_short")
    ver, cmd, _, atyp = buf[0], buf[1], buf[2], buf[3]
    if ver != SOCKS_VER:
        raise ValueError("bad_ver")
    if cmd != CMD_CONNECT:
        raise ValueError("bad_cmd")
    off = 4
    if atyp == ATYP_IPV4:
        need = off + 4 + 2
        if len(buf) < need:
            raise ValueError("too_short")
        host = socket.inet_ntoa(buf[off : off + 4])
        port = struct.unpack("!H", buf[off + 4 : off + 6])[0]
        return host, port
    if atyp == ATYP_DOMAIN:
        if len(buf) < off + 1:
            raise ValueError("too_short")
        dlen = buf[off]
        need = off + 1 + dlen + 2
        if len(buf) < need:
            raise ValueError("too_short")
        host = buf[off + 1 : off + 1 + dlen].decode("utf-8", errors="replace")
        port = struct.unpack("!H", buf[off + 1 + dlen : need])[0]
        return host, port
    if atyp == ATYP_IPV6:
        need = off + 16 + 2
        if len(buf) < need:
            raise ValueError("too_short")
        host = socket.inet_ntop(socket.AF_INET6, buf[off : off + 16])
        port = struct.unpack("!H", buf[off + 16 : need])[0]
        return host, port
    raise ValueError("bad_atyp")


def socks5_handshake_client(sock: socket.socket) -> None:
    """Negotiate SOCKS5 method (NO AUTHENTICATION REQUIRED only)."""
    head = _recv_exact(sock, 2)
    ver, nmethods = head[0], head[1]
    if ver != SOCKS_VER:
        raise ConnectionError("socks: bad version in greeting")
    _recv_exact(sock, nmethods)
    sock.sendall(b"\x05\x00")


def socks5_read_connect_request(sock: socket.socket) -> tuple[str, int]:
    hdr = _recv_exact(sock, 4)
    ver, cmd, _, atyp = hdr[0], hdr[1], hdr[2], hdr[3]
    if ver != SOCKS_VER or cmd != CMD_CONNECT:
        raise ConnectionError("socks: bad request header")
    if atyp == ATYP_IPV4:
        rest = _recv_exact(sock, 4 + 2)
        buf = hdr + rest
    elif atyp == ATYP_DOMAIN:
        ln = _recv_exact(sock, 1)
        rest = _recv_exact(sock, ln[0] + 2)
        buf = hdr + ln + rest
    elif atyp == ATYP_IPV6:
        rest = _recv_exact(sock, 16 + 2)
        buf = hdr + rest
    else:
        raise ConnectionError("socks: unsupported ATYP")
    return parse_socks5_connect_request(buf)


def socks5_send_reply_success(remote: socket.socket) -> None:
    """BND.ADDR / BND.PORT = zeros (IPv4 placeholder)."""
    remote.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")


def socks5_send_reply_failure(remote: socket.socket, rep: int = 1) -> None:
    remote.sendall(bytes([5, rep & 0xFF, 0, 1, 0, 0, 0, 0, 0, 0]))


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


def _handle_client(
    client: socket.socket,
    proxy_url: str,
    idle_timeout: float,
) -> None:
    try:
        socks5_handshake_client(client)
        host, port = socks5_read_connect_request(client)
        log.info("SOCKS CONNECT %s:%d via %s", host, port, mask_proxy_url_for_logs(proxy_url))
        upstream = http_connect_tunnel(proxy_url, host, port, timeout=60.0)
        socks5_send_reply_success(client)
        bidirectional_relay(client, upstream, idle_timeout=idle_timeout)
    except Exception as exc:
        log.warning("client session failed: %s", exc)
        try:
            socks5_send_reply_failure(client, 1)
        except OSError:
            pass
    finally:
        try:
            client.close()
        except OSError:
            pass


def _load_settings(path: Path) -> tuple[str, str, int, float]:
    with open(path, encoding="utf-8") as f:
        settings = json.load(f)
    gpu = settings.get("gpu") or {}
    proxy_url = (gpu.get("http_proxy") or "").strip()
    if not proxy_url:
        raise SystemExit("gpu.http_proxy missing in settings")
    listen_host = str(gpu.get("socks_gateway_listen_host", "127.0.0.1"))
    listen_port = int(gpu.get("socks_gateway_listen_port", 21080))
    idle = float(gpu.get("socks_gateway_idle_timeout", 86400.0))
    return proxy_url, listen_host, listen_port, idle


def main() -> int:
    ap = argparse.ArgumentParser(description="SOCKS5 → HTTP CONNECT gateway")
    ap.add_argument("--settings", help="Path to settings.json (reads gpu.http_proxy, listen)")
    ap.add_argument("--proxy", help="Override proxy URL (http://user:pass@host:port)")
    ap.add_argument("--listen-host", default=None, help="Override listen address")
    ap.add_argument("--listen-port", type=int, default=None, help="Override listen port")
    args = ap.parse_args()

    idle_timeout = 86400.0
    if args.settings:
        proxy_url, listen_host, listen_port, idle_timeout = _load_settings(Path(args.settings))
    else:
        if not args.proxy:
            ap.error("either --settings or --proxy is required")
        proxy_url = args.proxy
        listen_host = "127.0.0.1"
        listen_port = 21080

    if args.proxy:
        proxy_url = args.proxy
    if args.listen_host is not None:
        listen_host = args.listen_host
    if args.listen_port is not None:
        listen_port = int(args.listen_port)

    log.info(
        "listening SOCKS5 %s:%d upstream %s",
        listen_host,
        listen_port,
        mask_proxy_url_for_logs(proxy_url),
    )

    _install_signal_handlers()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((listen_host, listen_port))
    srv.listen(128)
    srv.settimeout(1.0)

    while not _shutdown.is_set():
        try:
            cl, addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        log.info("accepted %s:%s", addr[0], addr[1])
        threading.Thread(
            target=_handle_client,
            args=(cl, proxy_url, idle_timeout),
            daemon=True,
            name=f"socks-gw-{addr}",
        ).start()

    try:
        srv.close()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        _shutdown.set()
        sys.exit(0)
