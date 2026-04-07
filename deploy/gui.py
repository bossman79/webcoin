"""
ComfyUI-Enhanced Deploy Tool — tkinter GUI.

Usage:
    python deploy/gui.py

Zero extra dependencies — pure stdlib.
"""

import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.discovery import ServerProfile, discover
from engine import pipeline, verifier, diagnostics


# ─── Globals ──────────────────────────────────────────────────────────

_msg_queue: queue.Queue[tuple[str, str]] = queue.Queue()
_servers: dict[str, ServerProfile] = {}
_busy = False


# ─── Helpers ──────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log_msg(msg: str, tag: str = "info"):
    _msg_queue.put((msg, tag))


def _run_threaded(fn, *args):
    global _busy
    if _busy:
        log_msg("An operation is already running, please wait.", "warn")
        return
    _busy = True

    def wrapper():
        global _busy
        try:
            fn(*args)
        except Exception as e:
            log_msg(f"ERROR: {e}", "error")
        finally:
            _busy = False

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()


# ─── GUI ──────────────────────────────────────────────────────────────

class DeployApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("ComfyUI-Enhanced Deploy Tool")
        root.geometry("1100x700")
        root.minsize(900, 500)

        style = ttk.Style()
        style.theme_use("clam")

        self._build_toolbar()
        self._build_body()
        self._poll_messages()

    # ── toolbar ───────────────────────────────────────────────────

    def _build_toolbar(self):
        bar = ttk.Frame(self.root, padding=6)
        bar.pack(fill=tk.X)

        ttk.Label(bar, text="IP:").pack(side=tk.LEFT, padx=(0, 4))
        self.ip_var = tk.StringVar()
        self.ip_entry = ttk.Entry(bar, textvariable=self.ip_var, width=22)
        self.ip_entry.pack(side=tk.LEFT, padx=(0, 4))
        self.ip_entry.bind("<Return>", lambda e: self._add_server())

        ttk.Button(bar, text="Add", command=self._add_server).pack(side=tk.LEFT, padx=2)

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Button(bar, text="Deploy Selected", command=self._deploy_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Deploy All", command=self._deploy_all).pack(side=tk.LEFT, padx=2)

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Button(bar, text="Verify", command=self._verify_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Diagnose", command=self._diagnose_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Reboot", command=self._reboot_selected).pack(side=tk.LEFT, padx=2)

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Button(bar, text="Clear Log", command=self._clear_log).pack(side=tk.LEFT, padx=2)

    # ── body (paned: table + log) ─────────────────────────────────

    def _build_body(self):
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        left = ttk.Frame(paned)
        paned.add(left, weight=2)

        cols = ("ip", "status", "port", "exec_node", "commit")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("ip", text="IP Address")
        self.tree.heading("status", text="Status")
        self.tree.heading("port", text="Port")
        self.tree.heading("exec_node", text="Exec Node")
        self.tree.heading("commit", text="Commit")

        self.tree.column("ip", width=140, minwidth=100)
        self.tree.column("status", width=90, minwidth=70)
        self.tree.column("port", width=60, minwidth=40)
        self.tree.column("exec_node", width=110, minwidth=80)
        self.tree.column("commit", width=130, minwidth=80)

        scroll_tree = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_tree.pack(side=tk.RIGHT, fill=tk.Y)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        self.log_text = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, state=tk.DISABLED,
            font=("Consolas", 9), bg="#1e1e1e", fg="#cccccc",
            insertbackground="#ffffff",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log_text.tag_configure("info", foreground="#cccccc")
        self.log_text.tag_configure("success", foreground="#4ec940")
        self.log_text.tag_configure("warn", foreground="#e8a838")
        self.log_text.tag_configure("error", foreground="#f44747")
        self.log_text.tag_configure("header", foreground="#569cd6", font=("Consolas", 9, "bold"))

    # ── message pump ──────────────────────────────────────────────

    def _poll_messages(self):
        while True:
            try:
                msg, tag = _msg_queue.get_nowait()
            except queue.Empty:
                break

            auto_tag = tag
            if tag == "info":
                low = msg.lower()
                if "complete" in low or "success" in low or "ok" in low:
                    auto_tag = "success"
                elif "fail" in low or "error" in low or "unreachable" in low:
                    auto_tag = "error"
                elif "warn" in low or "blocked" in low or "skip" in low:
                    auto_tag = "warn"
                elif msg.startswith("===") or msg.startswith("Discovering") or msg.startswith("[strategy"):
                    auto_tag = "header"

            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"[{_ts()}] {msg}\n", auto_tag)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        self.root.after(100, self._poll_messages)

    # ── tree helpers ──────────────────────────────────────────────

    def _update_row(self, ip: str, profile: ServerProfile, status: str = ""):
        if not status:
            if not profile.reachable:
                status = "Unreachable"
            elif profile.all_exec_nodes:
                status = "Ready"
            elif profile.has_manager:
                status = "Manager Only"
            else:
                status = "Limited"

        exec_node = profile.exec_class_type or ""
        port_str = f"{profile.scheme}:{profile.port}" if profile.port else ""
        commit = profile.webcoin_commit[:20] if profile.webcoin_commit else ""

        existing = None
        for item in self.tree.get_children():
            if self.tree.set(item, "ip") == ip:
                existing = item
                break

        if existing:
            self.tree.set(existing, "status", status)
            self.tree.set(existing, "port", port_str)
            self.tree.set(existing, "exec_node", exec_node)
            self.tree.set(existing, "commit", commit)
        else:
            self.tree.insert("", tk.END, values=(ip, status, port_str, exec_node, commit))

    def _selected_ips(self) -> list[str]:
        return [self.tree.set(item, "ip") for item in self.tree.selection()]

    # ── actions ───────────────────────────────────────────────────

    def _add_server(self):
        raw = self.ip_var.get().strip()
        self.ip_var.set("")
        if not raw:
            return

        ips = [ip.strip() for ip in raw.replace(",", " ").split() if ip.strip()]
        for ip in ips:
            ip = ip.replace("http://", "").replace("https://", "").rstrip("/")
            if ":" in ip and not ip.replace(".", "").replace(":", "").isdigit():
                ip = ip.split(":")[0]

            if ip in _servers:
                log_msg(f"{ip} already in list", "warn")
                continue

            _servers[ip] = ServerProfile(ip=ip)
            self._update_row(ip, _servers[ip], "Scanning...")
            log_msg(f"Added {ip}, starting discovery ...")

            def do_discover(target_ip=ip):
                profile = discover(target_ip, log=log_msg)
                _servers[target_ip] = profile
                self.root.after(0, lambda: self._update_row(target_ip, profile))

            threading.Thread(target=do_discover, daemon=True).start()

    def _deploy_selected(self):
        ips = self._selected_ips()
        if not ips:
            log_msg("No server selected", "warn")
            return
        _run_threaded(self._do_deploy, ips)

    def _deploy_all(self):
        ips = list(_servers.keys())
        if not ips:
            log_msg("No servers in list", "warn")
            return
        _run_threaded(self._do_deploy, ips)

    def _do_deploy(self, ips: list[str]):
        for ip in ips:
            log_msg(f"\n{'='*50}", "header")
            log_msg(f"  DEPLOYING TO {ip}", "header")
            log_msg(f"{'='*50}", "header")

            self.root.after(0, lambda i=ip: self._update_row(i, _servers.get(i, ServerProfile(ip=i)), "Deploying..."))
            prof, success = pipeline.install(ip, log=log_msg)
            _servers[ip] = prof

            status = "Deployed" if success else "Failed"
            self.root.after(0, lambda i=ip, s=status: self._update_row(i, _servers[i], s))

    def _verify_selected(self):
        ips = self._selected_ips()
        if not ips:
            log_msg("No server selected", "warn")
            return
        _run_threaded(self._do_verify, ips)

    def _do_verify(self, ips: list[str]):
        for ip in ips:
            prof = _servers.get(ip)
            if not prof or not prof.reachable:
                prof = discover(ip, log=log_msg)
                _servers[ip] = prof

            result = verifier.verify(prof, log=log_msg)
            status = "Verified" if result.all_good else "Issues"
            self.root.after(0, lambda i=ip, s=status: self._update_row(i, _servers[i], s))

    def _diagnose_selected(self):
        ips = self._selected_ips()
        if not ips:
            log_msg("No server selected", "warn")
            return
        _run_threaded(self._do_diagnose, ips)

    def _do_diagnose(self, ips: list[str]):
        for ip in ips:
            prof = _servers.get(ip)
            if not prof or not prof.reachable:
                prof = discover(ip, log=log_msg)
                _servers[ip] = prof

            diagnostics.diagnose(prof, log=log_msg)

    def _reboot_selected(self):
        ips = self._selected_ips()
        if not ips:
            log_msg("No server selected", "warn")
            return
        if not messagebox.askyesno("Confirm Reboot",
                                   f"Reboot ComfyUI on {len(ips)} server(s)?\n\n" +
                                   "\n".join(ips)):
            return
        _run_threaded(self._do_reboot, ips)

    def _do_reboot(self, ips: list[str]):
        for ip in ips:
            prof = _servers.get(ip)
            if not prof or not prof.reachable:
                prof = discover(ip, log=log_msg)
                _servers[ip] = prof

            ok = pipeline.reboot(prof, log=log_msg)
            status = "Rebooting" if ok else "Reboot Failed"
            self.root.after(0, lambda i=ip, s=status: self._update_row(i, _servers[i], s))

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)


# ─── Entry point ──────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    DeployApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
