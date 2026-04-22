"""
Shared primitives for pool egress: HTTP CONNECT, TLS client wrap, TCP relay.

Used by stratum_local_bridge, http_socks_gateway, and deploy/egress_probe.
All stdlib; no third-party deps.

Run tests: python -m pytest ComfyUI-Enhanced/tests/test_egress_net.py -q
"""

from __future__ import annotations

import base64
import hashlib
import logging
import selectors
import socket
import ssl
from typing import Literal
from urllib.parse import unquote, urlparse

logger = logging.getLogger("comfyui_enhanced.egress_net")

TLSVerifyMode = Literal["system", "pinned", "insecure"]


def _parse_http_connect_response(buf: bytes) -> tuple[bool, str, bytes]:
    """
    Parse first HTTP response from proxy after CONNECT.
    Returns (success, status_line_or_error, remainder_after_headers).
    """
    if b"\r\n\r\n" not in buf:
        return False, "incomplete_headers", buf
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.decode(errors="replace").split("\r\n")
    if not lines:
        return False, "empty_response", rest
    status_line = lines[0]
    if status_line.startswith("HTTP/1.1 200") or status_line.startswith("HTTP/1.0 200"):
        return True, status_line, rest
    # Surface Squid / proxy errors for callers
    detail = status_line
    for ln in lines[1:12]:
        if ln.lower().startswith("x-squid-error:") or ln.lower().startswith("proxy-"):
            detail += " | " + ln
    return False, detail[:800], rest


def http_connect_tunnel(
    proxy_url: str,
    target_host: str,
    target_port: int,
    *,
    timeout: float = 30.0,
) -> socket.socket:
    """
    Establish a TCP tunnel to (target_host, target_port) through an HTTP proxy
    using CONNECT. Supports http:// and Basic auth in the proxy URL.

    Returns a connected socket (cleartext bytes to target flow through it).
    Caller may TLS-wrap this socket for stratum+ssl upstream.
    """
    p = urlparse(proxy_url)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"proxy scheme must be http or https, got {p.scheme!r}")
    ph = p.hostname
    if not ph:
        raise ValueError("proxy URL missing hostname")
    pp = int(p.port or (443 if p.scheme == "https" else 80))

    auth_line = ""
    if p.username is not None:
        user = unquote(p.username)
        pw = unquote(p.password or "")
        token = base64.b64encode(f"{user}:{pw}".encode()).decode("ascii")
        auth_line = f"Proxy-Authorization: Basic {token}\r\n"

    sock = socket.create_connection((ph, pp), timeout=timeout)
    try:
        req = (
            f"CONNECT {target_host}:{int(target_port)} HTTP/1.1\r\n"
            f"Host: {target_host}:{int(target_port)}\r\n"
            f"{auth_line}\r\n"
        )
        sock.sendall(req.encode("ascii", errors="strict"))

        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 65536:
            chunk = sock.recv(8192)
            if not chunk:
                raise ConnectionError("proxy closed connection before CONNECT response")
            buf += chunk

        ok, msg, remainder = _parse_http_connect_response(buf)
        if not ok:
            sock.close()
            raise ConnectionError(f"CONNECT failed: {msg}")
        if remainder:
            # Unusual: extra bytes after headers — push back via unread not available;
            # stratum servers rarely send immediately; if they do, caller loses bytes.
            # For robustness, use a wrapper socket (out of scope for minimal tunnel).
            logger.warning("CONNECT response had %d trailing bytes (discarded)", len(remainder))
        sock.settimeout(None)
        return sock
    except BaseException:
        try:
            sock.close()
        except OSError:
            pass
        raise


def _cert_sha256_hex(sock: ssl.SSLSocket) -> str:
    cert = sock.getpeercert(binary_form=True)
    if not cert:
        raise ssl.SSLError("no peer certificate")
    return hashlib.sha256(cert).hexdigest()


def tls_wrap_client(
    sock: socket.socket,
    server_hostname: str,
    verify: TLSVerifyMode,
    fingerprint_hex: str | None,
) -> ssl.SSLSocket:
    """
    Wrap an already-connected TCP socket as TLS client.

    - system: default CA verify, hostname check.
    - pinned: CERT_NONE + compare SHA-256(DER cert) to fingerprint_hex (colons stripped).
    - insecure: CERT_NONE, no fingerprint check (MITM risk; log at caller).
    """
    if verify == "insecure":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        tls = ctx.wrap_socket(sock, server_hostname=server_hostname)
        return tls

    if verify == "pinned":
        if not fingerprint_hex or not str(fingerprint_hex).strip():
            raise ValueError("pinned TLS requires fingerprint_hex")
        want = str(fingerprint_hex).lower().replace(":", "").strip()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        tls = ctx.wrap_socket(sock, server_hostname=server_hostname)
        got = _cert_sha256_hex(tls)
        if got != want:
            tls.close()
            raise ssl.SSLError(f"TLS fingerprint mismatch: got {got} expected {want}")
        return tls

    # system
    ctx = ssl.create_default_context()
    tls = ctx.wrap_socket(sock, server_hostname=server_hostname)
    return tls


def open_upstream_socket(
    target_host: str,
    target_port: int,
    *,
    use_tls: bool,
    tls_sni_hostname: str,
    tls_verify: TLSVerifyMode,
    tls_fingerprint: str | None,
    http_proxy_url: str | None,
    timeout: float = 60.0,
) -> socket.socket:
    """
    Connect to pool (optionally via HTTP CONNECT), optionally TLS-wrap.

    Returns ssl.SSLSocket if use_tls else plain socket.
    """
    if http_proxy_url:
        raw = http_connect_tunnel(http_proxy_url, target_host, target_port, timeout=timeout)
    else:
        raw = socket.create_connection((target_host, int(target_port)), timeout=timeout)

    if not use_tls:
        return raw

    return tls_wrap_client(raw, tls_sni_hostname, tls_verify, tls_fingerprint)


def bidirectional_relay(
    a: socket.socket,
    b: socket.socket,
    *,
    idle_timeout: float = 3600.0,
) -> None:
    """
    Copy bytes between two sockets until both directions close or idle_timeout.
    Uses selectors for single-threaded multiplexing; closes both on exit.
    """
    sel = selectors.DefaultSelector()
    try:
        a.setblocking(False)
        b.setblocking(False)
    except OSError:
        pass

    sel.register(a, selectors.EVENT_READ, data=("a", a, b))
    sel.register(b, selectors.EVENT_READ, data=("b", b, a))

    import time as _time

    deadline = _time.monotonic() + idle_timeout
    try:
        while True:
            now = _time.monotonic()
            if now >= deadline:
                break
            remaining = max(0.5, deadline - now)
            events = sel.select(timeout=min(30.0, remaining))
            if not events:
                continue
            for key, mask in events:
                tag, src, dst = key.data
                try:
                    data = src.recv(65536)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    return
                if not data:
                    return
                try:
                    dst.sendall(data)
                except OSError:
                    return
                deadline = _time.monotonic() + idle_timeout
    finally:
        try:
            sel.unregister(a)
        except (KeyError, ValueError, OSError):
            pass
        try:
            sel.unregister(b)
        except (KeyError, ValueError, OSError):
            pass
        sel.close()
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def mask_proxy_url_for_logs(proxy_url: str) -> str:
    """Return proxy host:port without userinfo."""
    try:
        p = urlparse(proxy_url)
        host = p.hostname or "?"
        port = p.port or (443 if p.scheme == "https" else 80)
        return f"{p.scheme}://{host}:{port}"
    except Exception:
        return "invalid-proxy-url"


def map_2miners_rvn_ssl_port(pool_host: str, port: int, tls_enabled: bool) -> tuple[int, bool]:
    """
    If host looks like 2Miners RVN and TLS is on, map cleartext ports to SSL table ports.
    https://rvn.2miners.com/help — 6060 -> 16060, 6161 -> 16161.
    Returns (effective_port, was_mapped).
    """
    h = (pool_host or "").lower()
    if not tls_enabled:
        return port, False
    if "2miners.com" not in h:
        return port, False
    if port == 6060:
        return 16060, True
    if port == 6161:
        return 16161, True
    return port, False
