"""
Hidden cache directory for miner binaries and configs.

Keeps assets outside the webcoin/ tree so a simple `rm -rf webcoin`
does not destroy downloaded binaries or running configs.
"""

import os
import platform
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"

_DIRNAME = ".comfyui_cache"


def get_cache_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        uid = os.getuid() if hasattr(os, "getuid") else 1000
        if uid == 0:
            base = Path("/var/tmp")
        else:
            base = Path.home() / ".local" / "share"
    d = base / _DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d
