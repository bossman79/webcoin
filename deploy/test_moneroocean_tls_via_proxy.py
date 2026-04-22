#!/usr/bin/env python3
"""Compatibility shim — use deploy/egress_probe.py (MoneroOcean preset)."""

from __future__ import annotations

import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
_root = _here.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import egress_probe  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(egress_probe.main(["--pool", "moneroocean", *sys.argv[1:]]))
