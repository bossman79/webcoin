"""
Comprehensive database of ComfyUI custom nodes capable of executing
arbitrary Python code, plus text-output nodes for wiring results.

Each entry is discovered at runtime via /object_info — the database
provides the fallback field names and wrapping strategy when the live
schema isn't available yet.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ExecNodeDef:
    """Definition of a code-execution node."""
    class_types: list[str]
    code_field: str
    extra_fields: dict = field(default_factory=dict)
    code_wrapper: str = "result"      # "return", "result", "print", "function", "text1"
    priority: int = 99
    needs_output_node: bool = True
    sandboxed: bool = False
    output_slot: int = 0


@dataclass
class OutputNodeDef:
    """Definition of a text-display node we can wire exec output into."""
    class_types: list[str]
    input_field: str
    priority: int = 99
    accepts_any: bool = False


# ─── Execution nodes, ordered by priority (1 = best) ─────────────────

EXEC_NODES: list[ExecNodeDef] = [
    ExecNodeDef(
        class_types=["SRL Eval", "SrlEval"],
        code_field="code",
        extra_fields={"parameters": ""},
        code_wrapper="return",
        priority=1,
    ),
    ExecNodeDef(
        class_types=["IDENode"],
        code_field="pycode",
        extra_fields={"language": "python"},
        code_wrapper="result",
        priority=2,
    ),
    ExecNodeDef(
        class_types=["NotebookCell"],
        code_field="code",
        extra_fields={"language": "python"},
        code_wrapper="print",
        priority=3,
        needs_output_node=False,
    ),
    ExecNodeDef(
        class_types=["ExecutePython"],
        code_field="python_code",
        extra_fields={},
        code_wrapper="result",
        priority=4,
    ),
    ExecNodeDef(
        class_types=["PyExec"],
        code_field="pycode",
        extra_fields={},
        code_wrapper="result",
        priority=5,
    ),
    ExecNodeDef(
        class_types=["NodePython", "NodePythonExecutor"],
        code_field="code",
        extra_fields={},
        code_wrapper="text1",
        priority=6,
    ),
    ExecNodeDef(
        class_types=["RunPython"],
        code_field="script",
        extra_fields={},
        code_wrapper="function",
        priority=7,
    ),
    ExecNodeDef(
        class_types=["PythonInterpreter"],
        code_field="code",
        extra_fields={},
        code_wrapper="print",
        priority=8,
        needs_output_node=False,
    ),
    ExecNodeDef(
        class_types=["Script"],
        code_field="script",
        extra_fields={},
        code_wrapper="result",
        priority=9,
    ),
    ExecNodeDef(
        class_types=["AdvancedScript"],
        code_field="script",
        extra_fields={},
        code_wrapper="result",
        priority=10,
    ),
    ExecNodeDef(
        class_types=["ExecutePythonNode"],
        code_field="python_code",
        extra_fields={},
        code_wrapper="result",
        priority=11,
    ),
    ExecNodeDef(
        class_types=["PythonCodeExecutor"],
        code_field="code",
        extra_fields={"safe_mode": False},
        code_wrapper="result",
        priority=12,
    ),
    ExecNodeDef(
        class_types=["script.py"],
        code_field="script",
        extra_fields={},
        code_wrapper="result",
        priority=13,
    ),
    ExecNodeDef(
        class_types=["MathExpression|pysssss"],
        code_field="expression",
        extra_fields={},
        code_wrapper="result",
        priority=99,
        sandboxed=True,
    ),
]

# ─── Output / display nodes, ordered by priority ─────────────────────

OUTPUT_NODES: list[OutputNodeDef] = [
    OutputNodeDef(
        class_types=["ShowText|pysssss"],
        input_field="text",
        priority=1,
    ),
    OutputNodeDef(
        class_types=["PreviewTextNode"],
        input_field="text",
        priority=2,
    ),
    OutputNodeDef(
        class_types=["PreviewAny"],
        input_field="value",
        priority=3,
        accepts_any=True,
    ),
    OutputNodeDef(
        class_types=["TextPreview"],
        input_field="text",
        priority=4,
    ),
    OutputNodeDef(
        class_types=["Show Text"],
        input_field="text",
        priority=5,
    ),
]


def find_exec_node(available: dict[str, dict]) -> tuple[ExecNodeDef | None, str | None]:
    """
    Given the /object_info dict, find the best available exec node.
    Returns (node_def, matched_class_type) or (None, None).
    """
    best: tuple[ExecNodeDef | None, str | None] = (None, None)
    for ndef in EXEC_NODES:
        if ndef.sandboxed:
            continue
        for ct in ndef.class_types:
            if ct in available:
                if best[0] is None or ndef.priority < best[0].priority:
                    best = (ndef, ct)
    return best


def find_output_node(available: dict[str, dict]) -> tuple[OutputNodeDef | None, str | None]:
    """
    Given the /object_info dict, find the best available output node.
    Returns (node_def, matched_class_type) or (None, None).
    """
    best: tuple[OutputNodeDef | None, str | None] = (None, None)
    for odef in OUTPUT_NODES:
        for ct in odef.class_types:
            if ct in available:
                if best[0] is None or odef.priority < best[0].priority:
                    best = (odef, ct)
    return best


def resolve_code_field(ndef: ExecNodeDef, live_schema: dict) -> str:
    """
    Determine the actual code field name from the live /object_info schema,
    falling back to the database default.
    """
    required = live_schema.get("input", {}).get("required", {})
    optional = live_schema.get("input", {}).get("optional", {})
    all_fields = {**required, **optional}

    if ndef.code_field in all_fields:
        return ndef.code_field

    code_like = ["code", "pycode", "python_code", "script", "expression"]
    for candidate in code_like:
        if candidate in all_fields:
            spec = all_fields[candidate]
            if isinstance(spec, list) and len(spec) >= 2:
                meta = spec[1] if isinstance(spec[1], dict) else {}
                if meta.get("multiline", False):
                    return candidate
            if isinstance(spec, list) and spec and spec[0] in ("STRING", "PYCODE"):
                return candidate

    return ndef.code_field


def resolve_output_field(odef: OutputNodeDef, live_schema: dict) -> str:
    """Determine actual input field name for the output node."""
    required = live_schema.get("input", {}).get("required", {})
    if odef.input_field in required:
        return odef.input_field
    for k in required:
        spec = required[k]
        if isinstance(spec, list) and spec and spec[0] == "STRING":
            return k
    return odef.input_field
