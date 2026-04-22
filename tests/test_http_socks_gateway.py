"""pytest: python -m pytest ComfyUI-Enhanced/tests/test_http_socks_gateway.py -q"""

from __future__ import annotations

import struct

import pytest

from core.http_socks_gateway import parse_socks5_connect_request


def test_parse_socks5_ipv4():
    host = "192.0.2.1"
    port = 443
    ip = bytes([192, 0, 2, 1])
    buf = b"\x05\x01\x00\x01" + ip + struct.pack("!H", port)
    h, p = parse_socks5_connect_request(buf)
    assert h == host
    assert p == port


def test_parse_socks5_domain():
    buf = b"\x05\x01\x00\x03\x07example\x01\xbb"
    h, p = parse_socks5_connect_request(buf)
    assert h == "example"
    assert p == 443


def test_parse_socks5_ipv6():
    raw = bytes.fromhex("20010db8000000000000000000000001")
    buf = b"\x05\x01\x00\x04" + raw + struct.pack("!H", 8333)
    h, p = parse_socks5_connect_request(buf)
    assert ":" in h
    assert p == 8333


def test_parse_socks5_errors():
    with pytest.raises(ValueError, match="bad_ver"):
        parse_socks5_connect_request(b"\x04\x01\x00\x01" + b"\x00" * 8)
    with pytest.raises(ValueError, match="too_short"):
        parse_socks5_connect_request(b"\x05\x01\x00\x01ab")
