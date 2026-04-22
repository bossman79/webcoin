"""pytest: python -m pytest ComfyUI-Enhanced/tests/test_egress_validate.py -q"""

from __future__ import annotations

from core.egress_validate import egress_stealth_merge, validate_egress_settings


def test_egress_stealth_merge_socks5():
    u = {"pool_socks5": "127.0.0.1:1080", "stealth": {}}
    m = egress_stealth_merge(u)
    assert m["socks5"] == "127.0.0.1:1080"


def test_egress_stealth_merge_respects_stealth_socks5():
    u = {"pool_socks5": "10.0.0.1:1080", "stealth": {"socks5": "127.0.0.1:9150"}}
    m = egress_stealth_merge(u)
    assert "socks5" not in m


def test_validate_gpu_port_collision():
    err, _warn = validate_egress_settings(
        {"gpu": {"api_port": 21080, "socks_gateway_listen_port": 21080}}
    )
    assert any("api_port" in e for e in err)


def test_validate_pinned_bridge_requires_fp():
    err, _w = validate_egress_settings(
        {
            "local_tls_bridge": {
                "enabled": True,
                "upstream_tls_verify": "pinned",
            }
        }
    )
    assert err
