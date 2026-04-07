"""
Server discovery — probes an IP to determine the best protocol/port,
available execution nodes, Manager status, and whether webcoin is installed.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

from . import http_client as http
from . import node_db

LOG_CB = Callable[[str], None]

SCAN_TARGETS = [
    ("https", 443),
    ("http", 80),
    ("http", 8188),
    ("http", 8888),
    ("https", 8188),
]


@dataclass
class ServerProfile:
    ip: str
    base_url: str = ""
    port: int = 0
    scheme: str = ""
    reachable: bool = False

    manager_version: str = ""
    security_level: str = ""
    has_manager: bool = False

    object_info: dict = field(default_factory=dict, repr=False)

    exec_node_def: node_db.ExecNodeDef | None = None
    exec_class_type: str | None = None
    exec_code_field: str = ""

    output_node_def: node_db.OutputNodeDef | None = None
    output_class_type: str | None = None
    output_input_field: str = ""

    all_exec_nodes: list[tuple[node_db.ExecNodeDef, str]] = field(default_factory=list)

    custom_nodes_path: str = ""
    webcoin_installed: bool = False
    webcoin_commit: str = ""


def _log(cb: LOG_CB | None, msg: str):
    if cb:
        cb(msg)


def scan_ports(ip: str, log: LOG_CB | None = None) -> tuple[str, str, int] | None:
    """Try multiple scheme/port combos. Returns (base_url, scheme, port) or None."""
    for scheme, port in SCAN_TARGETS:
        base = f"{scheme}://{ip}:{port}"
        _log(log, f"  Probing {base} ...")
        code, data = http.get_json(f"{base}/system_stats", timeout=8)
        if code == 200 and data:
            _log(log, f"  Found ComfyUI at {base}")
            return base, scheme, port
        if 200 <= code < 500 and code != 0:
            code2, _ = http.get(f"{base}/object_info", timeout=8)
            if code2 == 200:
                _log(log, f"  Found ComfyUI at {base} (system_stats={code})")
                return base, scheme, port
    return None


def probe_manager(base_url: str, log: LOG_CB | None = None) -> tuple[str, str]:
    """Check for ComfyUI Manager. Returns (version, security_level)."""
    code, data = http.get_json(f"{base_url}/manager/version", timeout=8)
    if code != 200 or not data:
        code, data = http.get_json(f"{base_url}/api/manager/version", timeout=8)

    version = ""
    if data and isinstance(data, dict):
        version = data.get("version", "")
    elif data and isinstance(data, str):
        version = data

    security = ""
    if version:
        code2, data2 = http.get_json(f"{base_url}/manager/security_level", timeout=5)
        if data2 and isinstance(data2, dict):
            security = data2.get("level", "")
        _log(log, f"  Manager {version}, security={security or 'unknown'}")

    return version, security


def discover_nodes(base_url: str, log: LOG_CB | None = None) -> dict:
    """Fetch /object_info and return the full dict."""
    _log(log, "  Fetching /object_info ...")
    code, data = http.get_json(f"{base_url}/object_info", timeout=20)
    if code == 200 and isinstance(data, dict):
        _log(log, f"  Found {len(data)} node types")
        return data
    _log(log, f"  /object_info failed ({code})")
    return {}


def pick_exec_nodes(
    object_info: dict, log: LOG_CB | None = None
) -> list[tuple[node_db.ExecNodeDef, str]]:
    """Return all available exec nodes sorted by priority."""
    found: list[tuple[node_db.ExecNodeDef, str]] = []
    for ndef in node_db.EXEC_NODES:
        if ndef.sandboxed:
            continue
        for ct in ndef.class_types:
            if ct in object_info:
                found.append((ndef, ct))
                break
    found.sort(key=lambda x: x[0].priority)
    if found:
        names = ", ".join(ct for _, ct in found)
        _log(log, f"  Exec nodes: {names}")
    else:
        _log(log, "  WARNING: No code-execution nodes found!")
    return found


def pick_output_nodes(
    object_info: dict, log: LOG_CB | None = None
) -> tuple[node_db.OutputNodeDef | None, str | None]:
    """Return best output/display node."""
    odef, ct = node_db.find_output_node(object_info)
    if ct:
        _log(log, f"  Output node: {ct}")
    return odef, ct


def discover(ip: str, log: LOG_CB | None = None) -> ServerProfile:
    """
    Full discovery pipeline for a single IP.
    Returns a populated ServerProfile.
    """
    profile = ServerProfile(ip=ip)
    _log(log, f"Discovering {ip} ...")

    result = scan_ports(ip, log)
    if result is None:
        _log(log, f"  {ip} — unreachable on all ports")
        return profile

    base_url, scheme, port = result
    profile.base_url = base_url
    profile.scheme = scheme
    profile.port = port
    profile.reachable = True

    version, security = probe_manager(base_url, log)
    profile.manager_version = version
    profile.security_level = security
    profile.has_manager = bool(version)

    oi = discover_nodes(base_url, log)
    profile.object_info = oi

    exec_nodes = pick_exec_nodes(oi, log)
    profile.all_exec_nodes = exec_nodes
    if exec_nodes:
        best_def, best_ct = exec_nodes[0]
        profile.exec_node_def = best_def
        profile.exec_class_type = best_ct
        live_schema = oi.get(best_ct, {})
        profile.exec_code_field = node_db.resolve_code_field(best_def, live_schema)

    odef, oct_ = pick_output_nodes(oi, log)
    profile.output_node_def = odef
    profile.output_class_type = oct_
    if odef and oct_:
        live_schema = oi.get(oct_, {})
        profile.output_input_field = node_db.resolve_output_field(odef, live_schema)

    _log(log, f"  Discovery complete for {ip}")
    return profile
