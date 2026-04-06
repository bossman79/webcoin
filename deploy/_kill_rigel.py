import subprocess, platform, os
lines = []
IS_WIN = platform.system() == "Windows"
if IS_WIN:
    for name in ["rigel.exe", "rigel", "t-rex.exe"]:
        try:
            r = subprocess.run(["taskkill", "/f", "/im", name], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                lines.append("KILLED " + name)
            else:
                lines.append(name + ": not found")
        except Exception as e:
            lines.append(name + ": " + str(e)[:40])
    # Also kill by path pattern
    try:
        r = subprocess.run(["wmic", "process", "where", "name='rigel.exe'", "delete"],
                           capture_output=True, text=True, timeout=10)
        lines.append("wmic rigel: " + r.stdout.strip()[:60])
    except:
        pass
else:
    for name in ["rigel", "t-rex"]:
        try:
            r = subprocess.run(["pkill", "-9", "-f", name], capture_output=True, text=True, timeout=5)
            lines.append(name + ": rc=" + str(r.returncode))
        except:
            pass
# Delete rigel files
for d in [os.getcwd(), os.path.expanduser("~")]:
    for f in os.listdir(d):
        fl = f.lower()
        if "rigel" in fl:
            fp = os.path.join(d, f)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                    lines.append("DELETED " + fp)
                elif os.path.isdir(fp):
                    import shutil
                    shutil.rmtree(fp)
                    lines.append("DELETED dir " + fp)
            except Exception as e:
                lines.append("del_err " + fp + ": " + str(e)[:40])
result = chr(10).join(lines)
