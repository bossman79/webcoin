"""Tests for core/public_proxy_lists.py (no network by default)."""

from __future__ import annotations

import json
from unittest.mock import patch

from core.public_proxy_lists import fetch_public_http_proxy_urls, fetch_public_socks5_proxy_urls


def test_fetch_public_http_parses_json():
    payload = [{"ip": "10.0.0.1", "port": 8080}, {"ip": "bad", "port": "x"}, {"ip": "", "port": 1}]

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return json.dumps(payload).encode()

    with patch("core.public_proxy_lists.urlopen", return_value=FakeResp()):
        urls = fetch_public_http_proxy_urls(timeout=1.0)
    assert "http://10.0.0.1:8080" in urls


def test_fetch_public_socks5_parses_lines():
    body = "1.2.3.4:1080\n\n#skip\n5.6.7.8:443\n"

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return body.encode()

    with patch("core.public_proxy_lists.urlopen", return_value=FakeResp()):
        urls = fetch_public_socks5_proxy_urls(timeout=1.0)
    assert "socks5://1.2.3.4:1080" in urls
    assert "socks5://5.6.7.8:443" in urls
