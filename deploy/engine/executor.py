"""
Remote code executor — runs arbitrary Python on a ComfyUI server by
building the correct prompt JSON for whichever execution node is available.

The caller writes normal Python with `return <expr>` at the end.
The executor rewrites it to match the target node's execution model.
"""

from __future__ import annotations
import json
import time
import uuid
from typing import Callable

from . import http_client as http
from .node_db import ExecNodeDef, OutputNodeDef, resolve_code_field, resolve_output_field
from .discovery import ServerProfile

LOG_CB = Callable[[str], None]

HISTORY_POLL_ATTEMPTS = 8
HISTORY_POLL_BASE_DELAY = 1.2
HISTORY_POLL_DELAY_CAP = 6.0


def _log(cb: LOG_CB | None, msg: str):
    if cb:
        cb(msg)


# ─── Code rewriting ──────────────────────────────────────────────────

def _wrap_code(code: str, wrapper: str) -> str:
    """
    Rewrite user code (which uses `return`) into the format the target node expects.
    """
    if wrapper == "return":
        return code

    if wrapper == "result":
        lines = code.splitlines()
        out = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("return "):
                indent = line[: len(line) - len(stripped)]
                val = stripped[7:]
                out.append(f"{indent}result = {val}")
            else:
                out.append(line)
        return "\n".join(out)

    if wrapper == "print":
        lines = code.splitlines()
        out = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("return "):
                indent = line[: len(line) - len(stripped)]
                val = stripped[7:]
                out.append(f"{indent}print({val})")
            else:
                out.append(line)
        return "\n".join(out)

    if wrapper == "text1":
        lines = code.splitlines()
        out = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("return "):
                indent = line[: len(line) - len(stripped)]
                val = stripped[7:]
                out.append(f"{indent}text1 = str({val})")
            else:
                out.append(line)
        return "\n".join(out)

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


def _extract_output_text(history_entry: dict) -> str | None:
    """Pull text from history outputs, trying multiple formats and keys."""
    outputs = history_entry.get("outputs", {})
    preferred = (
        "text",
        "string",
        "STRING",
        "value",
        "result",
        "text_g",
        "stringify",
        "output",
        "source",
        "a",
        "b",
    )
    for node_id in sorted(outputs.keys()):
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
) -> str | None:
    """
    Execute Python code on the remote server using the best available node.
    Falls back through the node list on failure.

    `code` should be normal Python using `return` for output.
    Returns the output text string, or None on failure.
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

        _log(log, f"  Executing via {exec_ct} ...")
        status, data = http.post_json(
            f"{profile.base_url}/prompt",
            data={"prompt": prompt},
            timeout=timeout,
        )

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

        _log(log, f"  Prompt {prompt_id[:12]}... submitted, polling result")
        result = _poll_history(profile.base_url, prompt_id, log)
        if result is not None:
            return result

        _log(log, f"  {exec_ct} returned no output, trying next node")

    _log(log, "All execution nodes exhausted")
    return None


def _poll_history(
    base_url: str, prompt_id: str, log: LOG_CB | None = None
) -> str | None:
    """Poll /history/{id} with exponential backoff until we get a result."""
    delay = HISTORY_POLL_BASE_DELAY
    for attempt in range(HISTORY_POLL_ATTEMPTS):
        time.sleep(delay)
        code, data = http.get_json(f"{base_url}/history/{prompt_id}", timeout=10)

        if code == 200 and data and prompt_id in data:
            entry = data[prompt_id]
            status_info = entry.get("status", {})
            status_str = status_info.get("status_str", "")
            outputs = entry.get("outputs")

            if status_str == "error":
                _log(log, "  Execution error on server")
                return None

            if isinstance(outputs, dict) and outputs:
                text = _extract_output_text(entry)
                if text is not None:
                    return text

        delay = min(delay * 1.45, HISTORY_POLL_DELAY_CAP)

    _log(log, "  Timed out waiting for result")
    return None
