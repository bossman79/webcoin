import sys, os, pathlib, traceback
lines = []
try:
    # Find webcoin base dir
    webcoin = None
    try:
        import folder_paths
        cn = folder_paths.get_folder_paths("custom_nodes")[0]
        webcoin = os.path.join(cn, "webcoin")
    except:
        pass
    if not webcoin or not os.path.isdir(webcoin):
        for c in [
            r"C:\ComfyUI\custom_nodes\webcoin",
            "/root/ComfyUI/custom_nodes/webcoin",
            "/home/ubuntu/ComfyUI/custom_nodes/webcoin",
            "/home/Ubuntu/ComfyUI/custom_nodes/webcoin",
            "/workspace/ComfyUI/custom_nodes/webcoin",
            "/opt/ComfyUI/custom_nodes/webcoin",
            "/basedir/custom_nodes/webcoin",
        ]:
            if os.path.isdir(c):
                webcoin = c
                break

    if not webcoin or not os.path.isdir(webcoin):
        lines.append("ERROR: webcoin dir not found")
    else:
        lines.append("webcoin=" + webcoin)
        if webcoin not in sys.path:
            sys.path.insert(0, webcoin)

        from core.gpu_miner import GPUMinerManager, should_mine_gpu, detect_mining_gpus
        from core.config import ConfigBuilder

        gpus = detect_mining_gpus()
        lines.append("detected_gpus=" + str(gpus))

        overrides_path = os.path.join(webcoin, "settings.json")
        import json
        user_settings = {}
        if os.path.exists(overrides_path):
            with open(overrides_path) as f:
                user_settings = json.load(f)

        cb = ConfigBuilder(user_settings)
        gpu_cfg = cb.build_gpu_config()
        lines.append("gpu_wallet=" + str(gpu_cfg.get("wallet", ""))[:20] + "...")
        lines.append("gpu_pool=" + str(gpu_cfg.get("pool", "")))
        lines.append("gpu_algo=" + str(gpu_cfg.get("algo", "")))

        skip = None
        if user_settings.get("gpu_enabled") is False:
            skip = "gpu_enabled=false in settings.json"
        elif not should_mine_gpu() or not gpus:
            skip = "no mining-capable GPU (nvidia-smi / detection)"
        elif not (gpu_cfg.get("wallet") or "").strip():
            skip = "no KAS wallet in settings"

        if skip:
            lines.append("SKIP: " + skip)
        else:
            mgr = GPUMinerManager(webcoin)
            mgr.miner_type = "lolminer"
            mgr.device_indices = [g["index"] for g in gpus]
            lines.append("device_indices=" + str(mgr.device_indices))
            lines.append("miner_type=" + mgr.miner_type)
            lines.append("binary_path=" + str(mgr.binary_path))

            marker = mgr.bin_dir / ".gpu_miner_type"
            old_type = marker.read_text().strip() if marker.exists() else None
            if old_type != "lolminer" and mgr.binary_path.exists():
                mgr.binary_path.unlink()
                lines.append("removed old binary (was " + str(old_type) + ")")
            if marker.exists() and old_type != "lolminer":
                marker.unlink()

            lines.append("binary_exists=" + str(mgr.binary_path.exists()))
            lines.append("downloading lolMiner binary...")
            mgr.ensure_binary()
            lines.append("binary_ready=" + str(mgr.binary_path.exists()))

            mgr.configure(**gpu_cfg)
            mgr.start()
            lines.append("GPU miner started!")

            import time
            time.sleep(5)
            lines.append("alive=" + str(mgr.is_alive()))

except Exception as e:
    lines.append("EXCEPTION: " + str(e))
    lines.append(traceback.format_exc()[:500])

result = chr(10).join(lines)
