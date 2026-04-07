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

HISTORY_POLL_ATTEMPTS = 12
HISTORY_POLL_BASE_DELAY = 2.0


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

def _extract_output_text(history_entry: dict) -> str | None:
    """Pull text from history outputs, trying multiple formats."""
    outputs = history_entry.get("outputs", {})
    for node_id in sorted(outputs.keys()):
        node_out = outputs[node_id]
        for key in ("text", "string", "value", "result"):
            val = node_out.get(key)
            if val is None:
                continue
            if isinstance(val, list):
                parts = [str(v) for v in val if v is not None]
                if parts:
                    return "\n".join(parts)
            elif isinstance(val, str):
                return val
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

            if status_str == "error":
                _log(log, "  Execution error on server")
                return None

            if status_str == "success" or entry.get("outputs"):
                text = _extract_output_text(entry)
                return text

        delay = min(delay * 1.5, 10.0)

    _log(log, "  Timed out waiting for result")
    return None
