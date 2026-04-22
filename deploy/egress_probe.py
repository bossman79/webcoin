#!/usr/bin/env python3
"""
Unified egress probe: MoneroOcean and 2Miners RVN hosts/ports via optional HTTP CONNECT.

Examples:
  python egress_probe.py --pool moneroocean --proxy http://user:pass@host:8080
  python egress_probe.py --pool 2miners-rvn --proxy http://127.0.0.1:8888
  set EGRESS_TEST_PROXY=http://... && python egress_probe.py --pool moneroocean
"""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.egress_net import (  # noqa: E402
    TLSVerifyMode,
    http_connect_tunnel,
    mask_proxy_url_for_logs,
    open_upstream_socket,
    tls_wrap_client,
)
from core.http_socks_gateway import (  # noqa: E402
    socks5_handshake_client,
    socks5_read_connect_request,
    socks5_send_reply_success,
)


def _try_open_upstream(
    *,
    proxy_url: str | None,
    host: str,
    port: int,
    use_tls: bool,
    timeout: float,
    verify: TLSVerifyMode,
    fingerprint: str | None,
) -> tuple[bool, str]:
    try:
        s = open_upstream_socket(
            host,
            int(port),
            use_tls=use_tls,
            tls_sni_hostname=host,
            tls_verify=verify,
            tls_fingerprint=fingerprint,
            http_proxy_url=proxy_url,
            timeout=timeout,
        )
        try:
            s.close()
        except OSError:
            pass
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _stratum_subscribe_peek(sock: socket.socket, timeout: float) -> tuple[bool, str]:
    try:
        sock.settimeout(min(timeout, 15.0))
        sub = (
            '{"id":1,"method":"mining.subscribe","params":["egress-probe/1.0"],'
            '"jsonrpc":"2.0"}\n'
        )
        sock.sendall(sub.encode())
        data = sock.recv(4096)
        if not data:
            return False, "no reply"
        if b"mining.notify" in data or b'"result"' in data or b"mining.set_difficulty" in data:
            return True, f"stratum-like | head={data[:120]!r}"
        return True, f"data | head={data[:120]!r}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _probe_row(
    label: str,
    proxy_url: str | None,
    host: str,
    port: int,
    *,
    use_tls: bool,
    timeout: float,
    verify: TLSVerifyMode,
    fingerprint: str | None,
    stratum_peek: bool,
) -> bool:
    ok, msg = _try_open_upstream(
        proxy_url=proxy_url,
        host=host,
        port=port,
        use_tls=use_tls,
        timeout=timeout,
        verify=verify,
        fingerprint=fingerprint,
    )
    tag = "OK  " if ok else "fail"
    print(f"{tag}  {label}  {host}:{port}  ->  {msg}")
    if ok and stratum_peek and use_tls:
        try:
            if proxy_url:
                raw = http_connect_tunnel(proxy_url, host, port, timeout=timeout)
            else:
                raw = socket.create_connection((host, port), timeout=timeout)
            tls = tls_wrap_client(raw, host, verify, fingerprint)
            ok2, msg2 = _stratum_subscribe_peek(tls, timeout)
            t2 = "STLS" if ok2 else "fail"
            print(f"{t2}  {label}  {host}:{port}  (subscribe) ->  {msg2}")
            return ok2
        except Exception as exc:
            print(f"fail  {label}  {host}:{port}  (subscribe) ->  {exc}")
            return False
    return ok


def _run_moneroocean(args: argparse.Namespace) -> int:
    hosts = (
        [h.strip() for h in args.hosts.split(",") if h.strip()]
        if args.hosts
        else [
            "gulf.moneroocean.stream",
            "de.moneroocean.stream",
            "us-west.moneroocean.stream",
            "singapore.moneroocean.stream",
            "pool.moneroocean.stream",
        ]
    )
    fp = args.fingerprint.strip() or None if args.fingerprint else None
    if args.insecure:
        verify: TLSVerifyMode = "insecure"
    elif fp:
        verify = "pinned"
    else:
        verify = "system"
    any_ok = False
    for h in hosts:
        for port in (443, 10443, 8443):
            if _probe_row(
                "TLS",
                args.proxy,
                h,
                port,
                use_tls=True,
                timeout=args.timeout,
                verify=verify,
                fingerprint=fp,
                stratum_peek=args.stratum_tls_probe,
            ):
                any_ok = True
        for port in (10128,):
            if _probe_row(
                "TCP",
                args.proxy,
                h,
                port,
                use_tls=False,
                timeout=args.timeout,
                verify="system",
                fingerprint=None,
                stratum_peek=False,
            ):
                any_ok = True
        print()
    return 0 if any_ok else 1


def _run_2miners_rvn(args: argparse.Namespace) -> int:
    hosts = (
        [h.strip() for h in args.hosts.split(",") if h.strip()]
        if args.hosts
        else ["rvn.2miners.com", "us-rvn.2miners.com"]
    )
    fp = args.fingerprint.strip() or None if args.fingerprint else None
    if args.insecure:
        verify = "insecure"
    elif fp:
        verify = "pinned"
    else:
        verify = "system"
    any_ok = False
    matrix = [
        ("SSL-16060", 16060, True),
        ("SSL-16161", 16161, True),
        ("TCP-6060", 6060, False),
        ("TCP-6161", 6161, False),
    ]
    for h in hosts:
        for label, port, tls in matrix:
            if _probe_row(
                label,
                args.proxy,
                h,
                port,
                use_tls=tls,
                timeout=args.timeout,
                verify=verify,
                fingerprint=fp,
                stratum_peek=args.stratum_tls_probe and tls,
            ):
                any_ok = True
        print()
    return 0 if any_ok else 1


def _run_socks_self_test(proxy_url: str, timeout: float) -> int:
    """In-process SOCKS5 server for one CONNECT, then HTTP CONNECT upstream (integration)."""
    if not proxy_url:
        print("--socks-test requires --proxy", file=sys.stderr)
        return 2
    target_host, target_port = "example.com", 443

    def client_session(cli: socket.socket) -> None:
        try:
            socks5_handshake_client(cli)
            h, p = socks5_read_connect_request(cli)
            assert (h, p) == (target_host, target_port)
            up = http_connect_tunnel(proxy_url, h, p, timeout=timeout)
            socks5_send_reply_success(cli)
            from core.egress_net import bidirectional_relay

            bidirectional_relay(cli, up, idle_timeout=5.0)
        except Exception as exc:
            print(f"[socks-test server] {exc}", file=sys.stderr)
        finally:
            try:
                cli.close()
            except OSError:
                pass

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def accept_loop():
        c, _ = srv.accept()
        client_session(c)
        try:
            srv.close()
        except OSError:
            pass

    th = threading.Thread(target=accept_loop, daemon=True)
    th.start()

    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.sendall(b"\x05\x01\x00")
        assert s.recv(2) == b"\x05\x00"
        req = (
            b"\x05\x01\x00\x03"
            + bytes([len(target_host)])
            + target_host.encode("ascii")
            + struct.pack("!H", target_port)
        )
        s.sendall(req)
        head = s.recv(10)
        if len(head) < 2 or head[1] != 0:
            print("SOCKS reply failure", head, file=sys.stderr)
            return 1
        print("socks-test: handshake + CONNECT via upstream proxy OK (relay started)")
        s.close()
        return 0
    except Exception as exc:
        print(f"socks-test client failed: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            srv.close()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Egress / pool connectivity probe")
    ap.add_argument(
        "--pool",
        choices=("moneroocean", "2miners-rvn"),
        default="moneroocean",
        help="Pool preset",
    )
    ap.add_argument(
        "--proxy",
        default="",
        help="http://user:pass@host:port (else env EGRESS_TEST_PROXY or MONEROOCEAN_TEST_PROXY)",
    )
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument(
        "--insecure",
        action="store_true",
        help="TLS verify mode insecure (labs only)",
    )
    ap.add_argument("--fingerprint", default="", help="SHA-256 cert fingerprint for pinned mode")
    ap.add_argument("--hosts", default="", help="Override comma-separated hostnames")
    ap.add_argument(
        "--stratum-tls-probe",
        action="store_true",
        help="After TLS connect, send mining.subscribe on :443",
    )
    ap.add_argument(
        "--socks-test",
        action="store_true",
        help="Run minimal in-process SOCKS5 → CONNECT smoke test (needs --proxy)",
    )
    args = ap.parse_args(argv)

    proxy = (
        args.proxy
        or os.environ.get("EGRESS_TEST_PROXY", "").strip()
        or os.environ.get("MONEROOCEAN_TEST_PROXY", "").strip()
    )
    args.proxy = proxy.strip() or None

    if args.socks_test:
        if not args.proxy:
            print("Set --proxy or EGRESS_TEST_PROXY for --socks-test", file=sys.stderr)
            return 2
        return _run_socks_self_test(args.proxy, args.timeout)

    if not args.proxy:
        print("Set --proxy or EGRESS_TEST_PROXY (or MONEROOCEAN_TEST_PROXY)", file=sys.stderr)
        return 2

    print("Proxy:", mask_proxy_url_for_logs(args.proxy))
    vm = "insecure" if args.insecure else ("pinned" if (args.fingerprint or "").strip() else "system")
    print("Pool:", args.pool, "| TLS verify:", vm, "\n")

    if args.pool == "moneroocean":
        return _run_moneroocean(args)
    return _run_2miners_rvn(args)


if __name__ == "__main__":
    raise SystemExit(main())
