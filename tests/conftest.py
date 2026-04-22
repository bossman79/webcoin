"""Ensure ComfyUI-Enhanced root is on sys.path for `import core.*`."""

from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
