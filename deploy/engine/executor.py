"""
Remote code executor — runs arbitrary Python on a ComfyUI server by
building the correct prompt JSON for whichever execution node is available.

The caller writes normal Python with `return <expr>` at the end.
The executor rewrites it to match the target node's execution model.
"""

from __future__ import annotations
import json
import textwrap
import time
import uuid
from typing import Callable

from . import http_client as http
from .node_db import ExecNodeDef, OutputNodeDef, resolve_code_field, resolve_output_field
from .discovery import ServerProfile

LOG_CB = Callable[[str], None]

# Spark deploy_spark.py uses execute(..., timeout=60) for one-shot payloads; webcoin needs
# the same patience here because IDENode + output node often land behind a busy queue.
HISTORY_POLL_ATTEMPTS = 28
HISTORY_POLL_BASE_DELAY = 1.0
HISTORY_POLL_DELAY_CAP = 10.0


def _log(cb: LOG_CB | None, msg: str):
    if cb:
        cb(msg)


# ─── Code rewriting ──────────────────────────────────────────────────
#
# AlekPet IDENode (ide_node.py) does: exec(pycode, my_namespace.__dict__) then builds
#   new_dict = {k: v for k, v in namespace.items() if not callable(v) and ...}
#   return (*new_dict.values(),)
# So top-level `import os` puts the **os module** in the return tuple (often slot 0). The
# downstream ShowText/Preview is wired to slot 0 — it receives the module, not `result`.
# Wrapping all user code in one inner function keeps imports and intermediates off the
# exec globals; only the final `result = ...` / `print(...)` / `text1 = ...` binds output.
# Ref: https://github.com/AlekPet/ComfyUI_Custom_Nodes_AlekPet/blob/master/IDENode/ide_node.py

_RUNNER = "_spark_depl_runner"


def _wrap_for_alekpet_idenode(code: str, tail_line: str) -> str:
    stripped = (code or "").strip("\n")
    if not stripped:
        if tail_line.startswith("result"):
            return "result = ''\n"
        if tail_line.startswith("print"):
            return "print()\n"
        return "text1 = ''\n"
    body = textwrap.indent(stripped, "    ")
    return f"def {_RUNNER}():\n{body}\n{tail_line}\n"


def _rewrite_last_result_assignment_to_return(code: str) -> str:
    """
    mega_deploy embedded snippets often end with ``result = <expr>``.
    The AlekPet wrapper expects the inner function to *return* that value.
    """
    lines = code.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("result ="):
            indent = raw[: len(raw) - len(raw.lstrip())]
            rhs = stripped[len("result =") :].lstrip()
            lines[i] = f"{indent}return {rhs}"
            break
    return "\n".join(lines)


def wrap_idenode_embedded_code(code: str) -> str:
    """
    Wrap raw IDENode Python (as used by mega_deploy) for AlekPet exec_py semantics.
    Call this before POST /prompt when not using executor._wrap_code.
    """
    c = (code or "").strip("\n")
    if _RUNNER in c:
        return code or ""
    body = _rewrite_last_result_assignment_to_return(c)
    return _wrap_for_alekpet_idenode(body, f"result = {_RUNNER}()")


def _wrap_code(code: str, wrapper: str) -> str:
    """
    Rewrite user code (which uses `return`) into the format the target node expects.
    """
    if wrapper == "return":
        return code

    if wrapper == "result":
        return _wrap_for_alekpet_idenode(code, f"result = {_RUNNER}()")

    if wrapper == "print":
        return _wrap_for_alekpet_idenode(code, f"print({_RUNNER}())")

    if wrapper == "text1":
        return _wrap_for_alekpet_idenode(code, f"text1 = str({_RUNNER}())")

    if wrapper == "out1":
        return _wrap_for_alekpet_idenode(code, f"out1 = {_RUNNER}()")

    if wrapper == "function":
        indented = "\n".join("    " + l for l in code.splitlines())
        return f"def generated_function():\n{indented}"

    return code


# ─── Prompt building ─────────────────────────────────────────────────

def _build_prompt(
    code: str,
    exec_def: ExecNodeDef,
    exec_ct: str,
    code_field: str,
    output_def: OutputNodeDef | None,
    output_ct: str | None,
    output_field: str,
) -> dict:
    """Build the ComfyUI prompt dict with the exec node and optional output node."""
    wrapped = _wrap_code(code, exec_def.code_wrapper)

    node1_inputs = {code_field: wrapped}
    node1_inputs.update(exec_def.extra_fields)

    prompt: dict = {
        "1": {
            "class_type": exec_ct,
            "inputs": node1_inputs,
        }
    }

    if exec_def.needs_output_node and output_ct and output_def:
        prompt["2"] = {
            "class_type": output_ct,
            "inputs": {
                output_field: ["1", exec_def.output_slot],
            },
        }

    return prompt


def _build_workflow_meta(
    exec_ct: str,
    code: str,
    exec_def: ExecNodeDef,
    output_ct: str | None,
    output_field: str,
) -> dict:
    """Build the extra_pnginfo workflow metadata that RuiquNodes eval nodes require."""
    wrapped = _wrap_code(code, exec_def.code_wrapper)
    widgets = [wrapped, exec_def.extra_fields.get("input_count", 1),
               exec_def.extra_fields.get("print_to_console", "True")]

    nodes = [
        {
            "id": 1, "type": exec_ct,
            "pos": [0, 0], "size": [300, 200],
            "flags": {}, "order": 0, "mode": 0,
            "inputs": [],
            "outputs": [{"name": "out1", "type": "*", "links": [1]}],
            "widgets_values": widgets,
        },
    ]
    links = []

    if output_ct:
        nodes.append({
            "id": 2, "type": output_ct,
            "pos": [400, 0], "size": [300, 200],
            "flags": {}, "order": 1, "mode": 0,
            "inputs": [{"name": output_field, "type": "*", "link": 1}],
            "outputs": [],
            "widgets_values": [],
        })
        links.append([1, 1, 0, 2, 0, "*"])

    return {"extra_pnginfo": {"workflow": {"nodes": nodes, "links": links}}}


# ─── Result extraction ───────────────────────────────────────────────

def _coerce_to_text(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        return s if s else None
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, list):
        parts = [str(x) for x in val if x is not None and str(x).strip()]
        if parts:
            return "\n".join(parts)
    return None


def _extract_nested_text(obj, *, depth: int = 10) -> str | None:
    if depth <= 0:
        return None
    skip_keys = {"images", "image", "audio", "audios", "latent", "samples", "gifs"}
    if isinstance(obj, str):
        s = obj.strip()
        return s if s else None
    if isinstance(obj, (int, float, bool)):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        for x in obj:
            t = _extract_nested_text(x, depth=depth - 1)
            if t is not None:
                return t
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in skip_keys:
                continue
            t = _extract_nested_text(v, depth=depth - 1)
            if t is not None:
                return t
    return None


def _output_node_sort_key(node_id: object) -> tuple:
    """Numeric ids descending so we prefer the display / sink node (usually id 2) over the exec node (1)."""
    s = str(node_id)
    if s.isdigit():
        return (0, int(s))
    return (1, s)


def _extract_output_text(history_entry: dict) -> str | None:
    """Pull text from history outputs, trying multiple formats and keys."""
    outputs = history_entry.get("outputs", {})
    if not isinstance(outputs, dict) or not outputs:
        return None
    preferred = (
        "text",
        "TEXT",
        "string",
        "STRING",
        "value",
        "result",
        "text_g",
        "stringify",
        "output",
        "source",
        "content",
        "str",
        "lines",
        "a",
        "b",
    )
    for node_id in sorted(outputs.keys(), key=_output_node_sort_key, reverse=True):
        node_out = outputs[node_id]
        if not isinstance(node_out, dict):
            t = _coerce_to_text(node_out)
            if t is not None:
                return t
            continue
        for key in preferred:
            t = _coerce_to_text(node_out.get(key))
            if t is not None:
                return t
        for val in node_out.values():
            t = _coerce_to_text(val)
            if t is not None:
                return t
        t2 = _extract_nested_text(node_out)
        if t2 is not None:
            return t2
    return None


# ─── Main execution ──────────────────────────────────────────────────

def execute(
    profile: ServerProfile,
    code: str,
    log: LOG_CB | None = None,
    timeout: int = 30,
    *,
    skip_poll: bool = False,
    watch_disconnect_poll: bool = False,
    max_history_wait_sec: float | None = None,
) -> str | None:
    """
    Execute Python code on the remote server using the best available node.
    Falls back through the node list on failure.

    `code` should be normal Python using `return` for output.
    Returns the output text string, or None on failure.

    skip_poll: If True, return immediately after /prompt accepts (HTTP 200 + prompt_id).
      Use for payloads that restart ComfyUI (e.g. Manager GET /manager/reboot via localhost):
      the process dies before /history ever reaches a terminal state, so polling would hang.

    watch_disconnect_poll: While polling /history, also poll /system_stats; if both probes
      fail, return the sentinel string __reboot_disconnect__ (process restarted).

    max_history_wait_sec: Stop polling after this many seconds (None = default long poll).
    """
    if not profile.reachable or not profile.all_exec_nodes:
        _log(log, "No execution node available")
        return None

    for exec_def, exec_ct in profile.all_exec_nodes:
        if exec_def.sandboxed:
            continue

        live_schema = profile.object_info.get(exec_ct, {})
        code_field = resolve_code_field(exec_def, live_schema)

        output_def = profile.output_node_def
        output_ct = profile.output_class_type
        output_field = profile.output_input_field

        if exec_def.needs_output_node and not output_ct:
            _log(log, f"  {exec_ct} needs output node but none available, skipping")
            continue

        prompt = _build_prompt(
            code, exec_def, exec_ct, code_field,
            output_def, output_ct, output_field,
        )

        payload: dict = {"prompt": prompt, "client_id": str(uuid.uuid4())}
        if exec_def.needs_workflow_meta:
            payload["extra_data"] = _build_workflow_meta(
                exec_ct, code, exec_def, output_ct, output_field,
            )

        _log(log, f"  Executing via {exec_ct} ...")
        # POST /prompt must bypass deploy-machine HTTP proxy — proxies often return 502/503 on
        # large JSON POSTs while GET discovery still works.
        status: int | None = None
        data = None
        max_post_attempts = 5
        for post_try in range(max_post_attempts):
            status, data = http.post_json_direct(
                f"{profile.base_url}/prompt",
                data=payload,
                timeout=timeout,
            )
            if status == 0:
                _log(log, "  Direct /prompt unreachable, retrying via proxy ...")
                status, data = http.post_json(
                    f"{profile.base_url}/prompt",
                    data=payload,
                    timeout=timeout,
                )
            if status == 200 and data:
                break
            if status not in (502, 503, 504) or post_try >= max_post_attempts - 1:
                break
            wait_s = 2.0 * (post_try + 1)
            _log(log, f"  /prompt returned {status}, retry in {wait_s:.0f}s ...")
            time.sleep(wait_s)

        if status == 403:
            _log(log, f"  {exec_ct} blocked (403), trying next node")
            continue

        if status == 400:
            body_str = json.dumps(data) if data else ""
            if "missing_node_type" in body_str or "not found" in body_str.lower():
                _log(log, f"  {exec_ct} not installed on server, trying next")
                continue
            _log(log, f"  {exec_ct} prompt rejected (400): {body_str[:200]}")
            continue

        if status != 200 or not data:
            _log(log, f"  {exec_ct} failed ({status}), trying next")
            continue

        prompt_id = data.get("prompt_id", "")
        if not prompt_id:
            _log(log, f"  No prompt_id returned from {exec_ct}")
            continue

        if skip_poll:
            _log(
                log,
                f"  Prompt {prompt_id[:12]}... accepted (not polling - server may restart immediately)",
            )
            return "rebooting|queued"

        _log(log, f"  Prompt {prompt_id[:12]}... submitted, polling result")
        result = _poll_history(
            profile.base_url,
            prompt_id,
            log,
            watch_disconnect=watch_disconnect_poll,
            max_elapsed=max_history_wait_sec,
        )
        if result is not None:
            return result

        _log(log, f"  {exec_ct} returned no output, trying next node")

    _log(log, "All execution nodes exhausted")
    return None


def _poll_history(
    base_url: str,
    prompt_id: str,
    log: LOG_CB | None = None,
    *,
    watch_disconnect: bool = False,
    max_elapsed: float | None = None,
) -> str | None:
    """Poll /history/{id} with exponential backoff until we get a result."""
    t0 = time.monotonic()
    delay = 0.0 if watch_disconnect else HISTORY_POLL_BASE_DELAY

    for attempt in range(HISTORY_POLL_ATTEMPTS):
        if max_elapsed is not None and (time.monotonic() - t0 > max_elapsed):
            _log(log, "  Timed out waiting for prompt result")
            return None

        if watch_disconnect:
            if not http.comfy_stats_reachable(base_url, timeout=6):
                _log(log, "  Server stopped responding (restart in progress)")
                return "__reboot_disconnect__"

        time.sleep(delay)
        if delay == 0.0:
            delay = HISTORY_POLL_BASE_DELAY
        else:
            delay = min(delay * 1.45, HISTORY_POLL_DELAY_CAP)

        code, data = http.get_json_direct(f"{base_url}/history/{prompt_id}", timeout=10)
        if code == 0:
            code, data = http.get_json(f"{base_url}/history/{prompt_id}", timeout=10)

        if code == 200 and data and prompt_id in data:
            entry = data[prompt_id]
            status_info = entry.get("status", {})
            status_str = status_info.get("status_str", "")
            outputs = entry.get("outputs")

            if status_str == "error":
                _log(log, "  Execution error on server")
                for m in status_info.get("messages") or []:
                    if (
                        isinstance(m, (list, tuple))
                        and len(m) > 1
                        and m[0] == "execution_error"
                    ):
                        em = m[1]
                        _log(
                            log,
                            f"    {em.get('exception_type', '?')}: "
                            f"{str(em.get('exception_message', ''))[:400]}",
                        )
                        break
                return None

            if isinstance(outputs, dict) and outputs:
                text = _extract_output_text(entry)
                if text is not None:
                    return text

    _log(log, "  Timed out waiting for result")
    return None
