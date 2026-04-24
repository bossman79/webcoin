#!/usr/bin/env python3
"""
Run **on the ComfyUI host** (not the deploy workstation).

Resolves ``custom_nodes`` the same way as ``engine.pipeline.FIND_CUSTOM_NODES`` /
``mega_deploy.FIND_CUSTOM_NODES``: ``folder_paths`` when Comfy's root is on
``sys.path`` (see COMFYUI_ROOT), then the same static path list + home/base tails,
then Windows paths from mega_deploy. Then git clone webcoin, pip install, remove
stale markers so Comfy reload picks up orchestration.

Usage (after copying this file to the host):

  python3 host_webcoin_setup.py

Optional:

  COMFYUI_ROOT=/path/to/ComfyUI python3 host_webcoin_setup.py
  python3 host_webcoin_setup.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

REPO_URL = "https://github.com/bossman79/webcoin.git"


def find_custom_nodes() -> str:
    """
    Mirror deploy FIND_CUSTOM_NODES: folder_paths (if importable), static dirs,
    base+tail combinations, ~ tails, Windows paths.
    """
    try:
        comfy_root = (os.environ.get("COMFYUI_ROOT") or "").strip()
        if comfy_root and os.path.isdir(comfy_root) and comfy_root not in sys.path:
            sys.path.insert(0, comfy_root)

        try:
            import folder_paths  # type: ignore

            if hasattr(folder_paths, "get_folder_paths") and callable(
                getattr(folder_paths, "get_folder_paths")
            ):
                try:
                    cns = folder_paths.get_folder_paths("custom_nodes")
                except (KeyError, TypeError, AttributeError):
                    cns = None
                if cns is not None:
                    if isinstance(cns, dict):
                        for v in cns.values():
                            if isinstance(v, (list, tuple)):
                                for c in v:
                                    if isinstance(c, str) and c and os.path.isdir(c):
                                        return c
                            elif isinstance(v, str) and v and os.path.isdir(v):
                                return v
                    elif not isinstance(cns, (list, tuple)):
                        cns = [cns]
                    for c in cns:
                        if isinstance(c, str) and c and os.path.isdir(c):
                            return c
            fnp = getattr(folder_paths, "folder_names_and_paths", None)
            if isinstance(fnp, dict):
                entry = fnp.get("custom_nodes")
                if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                    paths = entry[0]
                    if isinstance(paths, list):
                        for c in paths:
                            if isinstance(c, str) and c and os.path.isdir(c):
                                return c
            fp = getattr(folder_paths, "__file__", None)
            if isinstance(fp, str) and fp:
                d = os.path.join(os.path.dirname(fp), "custom_nodes")
                if os.path.isdir(d):
                    return d
        except Exception:
            pass

        for g in (
            "/app/ComfyUI/custom_nodes",
            "/opt/ComfyUI/custom_nodes",
            "/root/ComfyUI/custom_nodes",
            "/workspace/ComfyUI/custom_nodes",
            "/data/ComfyUI/custom_nodes",
            "/basedir/custom_nodes",
            "/comfy/ComfyUI/custom_nodes",
            "/usr/local/ComfyUI/custom_nodes",
            "/mnt/ComfyUI/custom_nodes",
            "/export/ComfyUI/custom_nodes",
            "/home/user/ComfyUI/custom_nodes",
            "/home/ubuntu/ComfyUI/custom_nodes",
            "/var/ComfyUI/custom_nodes",
            r"C:\Program Files\ComfyUI-aki-v2\ComfyUI\custom_nodes",
            r"C:\ComfyUI\custom_nodes",
        ):
            if os.path.isdir(g):
                return g

        tails = (
            "ComfyUI/custom_nodes",
            "comfyui/custom_nodes",
            "ComfyUI/ComfyUI/custom_nodes",
        )
        for base in (
            "/root",
            "/app",
            "/data",
            "/workspace",
            "/opt",
            "/srv",
            "/export",
            "/mnt",
            "/var",
        ):
            if not os.path.isdir(base):
                continue
            for t in tails:
                p = os.path.join(base, *t.split("/"))
                if os.path.isdir(p):
                    return p

        home = os.path.expanduser("~")
        if isinstance(home, str) and home:
            for t in tails:
                p = os.path.join(home, *t.split("/"))
                if os.path.isdir(p):
                    return p
    except Exception:
        pass
    return ""


def _git_pip_wc(cn: str, dry_run: bool) -> int:
    wc = os.path.join(cn, "webcoin")
    if dry_run:
        print(f"dry-run: would use cn={cn!r} wc={wc!r}")
        return 0

    if os.path.isdir(wc):
        shutil.rmtree(wc, ignore_errors=True)
    r = subprocess.run(
        ["git", "clone", "--depth", "1", REPO_URL, "webcoin"],
        cwd=cn,
        capture_output=True,
        text=True,
        timeout=180,
    )
    print(f"git clone exit={r.returncode}")
    if r.stderr.strip():
        print(r.stderr.strip()[:500])
    if r.returncode != 0 or not os.path.isfile(os.path.join(wc, "__init__.py")):
        print("clone failed or missing __init__.py", file=sys.stderr)
        return 1

    req = os.path.join(wc, "requirements.txt")
    if os.path.isfile(req):
        r2 = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", req],
            capture_output=True,
            text=True,
            timeout=300,
        )
        print(f"pip install exit={r2.returncode}")
        if r2.stderr.strip():
            print(r2.stderr.strip()[:400])

    for marker in (".initialized", ".orch.pid"):
        mp = os.path.join(wc, marker)
        if os.path.isfile(mp):
            try:
                os.remove(mp)
                print(f"removed {marker}")
            except OSError as e:
                print(f"could not remove {marker}: {e}")

    print("OK: webcoin installed under", wc)
    print("Restart the ComfyUI process (or reload custom nodes) so it loads and runs.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print resolved custom_nodes path",
    )
    args = ap.parse_args()

    cn = find_custom_nodes()
    if not cn:
        print(
            "Could not find custom_nodes. Set COMFYUI_ROOT to your ComfyUI checkout "
            "(directory containing folder_paths.py) and retry, or install ComfyUI "
            "under a standard path.",
            file=sys.stderr,
        )
        return 1

    print("custom_nodes:", cn)
    return _git_pip_wc(cn, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
