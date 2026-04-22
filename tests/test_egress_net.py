"""pytest: python -m pytest ComfyUI-Enhanced/tests/test_egress_net.py -q"""

from __future__ import annotations

import socket
import ssl

import pytest

from core.egress_net import (
    TLSVerifyMode,
    _parse_http_connect_response,
    bidirectional_relay,
    http_connect_tunnel,
    map_2miners_rvn_ssl_port,
    mask_proxy_url_for_logs,
    tls_wrap_client,
)


def test_parse_connect_response_ok():
    raw = b"HTTP/1.1 200 Connection established\r\n\r\n"
    ok, msg, rest = _parse_http_connect_response(raw)
    assert ok
    assert "200" in msg
    assert rest == b""


def test_parse_connect_response_fail_squid():
    raw = (
        b"HTTP/1.1 503 Service Unavailable\r\n"
        b"X-Squid-Error: ERR_DNS_FAIL 0\r\n"
        b"\r\n"
    )
    ok, msg, rest = _parse_http_connect_response(raw)
    assert not ok
    assert "503" in msg
    assert "ERR_DNS_FAIL" in msg


def test_parse_connect_response_incomplete():
    ok, msg, rest = _parse_http_connect_response(b"HTTP/1.1 200")
    assert not ok
    assert "incomplete" in msg


def test_mask_proxy_url_for_logs():
    m = mask_proxy_url_for_logs("http://user:secret@proxy.example.com:8080")
    assert "secret" not in m
    assert "proxy.example.com:8080" in m


def test_map_2miners_rvn_ssl_port():
    assert map_2miners_rvn_ssl_port("rvn.2miners.com", 6060, True) == (16060, True)
    assert map_2miners_rvn_ssl_port("us-rvn.2miners.com", 6161, True) == (16161, True)
    assert map_2miners_rvn_ssl_port("rvn.2miners.com", 16060, True) == (16060, False)
    assert map_2miners_rvn_ssl_port("rvn.2miners.com", 6060, False) == (6060, False)
    assert map_2miners_rvn_ssl_port("other.pool", 6060, True) == (6060, False)


def test_http_connect_tunnel_mocked(monkeypatch):
    """Inject fake proxy that returns 200 then behaves as passthrough."""

    class FakeSock:
        def __init__(self):
            self.sent = b""
            self._buf = (
                b"HTTP/1.1 200 Connection established\r\n"
                b"Proxy-Agent: test\r\n\r\n"
            )
            self.closed = False

        def sendall(self, d: bytes):
            self.sent += d

        def recv(self, n: int):
            if self._buf:
                out = self._buf[:n]
                self._buf = self._buf[n:]
                return out
            return b""

        def settimeout(self, _t):
            pass

        def close(self):
            self.closed = True

    fake = FakeSock()

    def fake_create_connection(addr, timeout=30):
        assert addr == ("proxy.local", 8080)
        return fake

    monkeypatch.setattr("core.egress_net.socket.create_connection", fake_create_connection)

    s = http_connect_tunnel(
        "http://u:p@proxy.local:8080",
        "pool.example",
        443,
        timeout=5.0,
    )
    assert b"CONNECT pool.example:443" in fake.sent
    assert b"Proxy-Authorization" in fake.sent
    assert s is fake


def test_http_connect_tunnel_rejects_non_200(monkeypatch):
    class FakeSock:
        def __init__(self):
            self._buf = b"HTTP/1.1 403 Forbidden\r\n\r\n"

        def sendall(self, d: bytes):
            pass

        def recv(self, n: int):
            if self._buf:
                o, self._buf = self._buf, b""
                return o
            return b""

        def settimeout(self, _t):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        "core.egress_net.socket.create_connection",
        lambda addr, timeout=30: FakeSock(),
    )

    with pytest.raises(ConnectionError, match="CONNECT failed"):
        http_connect_tunnel("http://proxy:8080", "x.com", 80, timeout=5.0)


def test_bidirectional_relay_idle_closes_sockets():
    a, b = socket.socketpair()
    try:
        bidirectional_relay(a, b, idle_timeout=0.15)
    finally:
        pass
    with pytest.raises(OSError):
        a.send(b"x")


def test_tls_wrap_client_insecure_loopback_ssl(monkeypatch):
    """Pinned mode: mock SSLSocket.getpeercert after wrap to avoid real server."""
    sent = b""

    class FakeRaw:
        def __init__(self):
            self.closed = False

        def send(self, d: bytes):
            nonlocal sent
            sent += d
            return len(d)

        def recv(self, n: int):
            return b""

        def settimeout(self, _t):
            pass

        def close(self):
            self.closed = True

    der = b"\x30\x03\x01\x02\x03"  # fake DER
    fp = __import__("hashlib").sha256(der).hexdigest()

    class FakeTls(ssl.SSLSocket):
        def __init__(self):
            pass

        def getpeercert(self, binary_form: bool = False):
            if binary_form:
                return der
            return {}

        def close(self):
            pass

    def fake_wrap(self, sock, *, server_hostname=None, **kw):
        return FakeTls()

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", fake_wrap)

    tls = tls_wrap_client(FakeRaw(), "example.com", "pinned", fp)
    assert isinstance(tls, FakeTls)

    wrong = "00" * 32
    with pytest.raises(ssl.SSLError, match="fingerprint mismatch"):
        tls_wrap_client(FakeRaw(), "example.com", "pinned", wrong)


def test_tls_verify_mode_type():
    v: TLSVerifyMode = "system"
    assert v == "system"
