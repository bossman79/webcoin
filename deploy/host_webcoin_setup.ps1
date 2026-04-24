#Requires -Version 5.1
<#
.SYNOPSIS
  Run ON the ComfyUI machine. Finds custom_nodes the same way as deploy FIND_CUSTOM_NODES
  (static paths + home tails + optional COMFYUI_ROOT), then git clone webcoin, pip install,
  clears .initialized / .orch.pid. Restart ComfyUI yourself (or fix the service name below).

  Usage:
    .\host_webcoin_setup.ps1
    $env:COMFYUI_ROOT = 'D:\ComfyUI'; .\host_webcoin_setup.ps1
    .\host_webcoin_setup.ps1 -DryRun
#>
param(
    [string] $ComfyUiRoot = $env:COMFYUI_ROOT,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/bossman79/webcoin.git'

function Get-CustomNodesDir {
    param([string] $RootHint)

    if ($RootHint) {
        $c1 = Join-Path $RootHint 'custom_nodes'
        if (Test-Path -LiteralPath $c1 -PathType Container) { return $c1 }
    }

    $globalPaths = @(
        'C:\Program Files\ComfyUI-aki-v2\ComfyUI\custom_nodes',
        'C:\ComfyUI\custom_nodes',
        '/app/ComfyUI/custom_nodes',
        '/opt/ComfyUI/custom_nodes',
        '/root/ComfyUI/custom_nodes',
        '/workspace/ComfyUI/custom_nodes',
        '/data/ComfyUI/custom_nodes',
        '/basedir/custom_nodes',
        '/comfy/ComfyUI/custom_nodes',
        '/usr/local/ComfyUI/custom_nodes',
        '/mnt/ComfyUI/custom_nodes',
        '/export/ComfyUI/custom_nodes',
        '/home/user/ComfyUI/custom_nodes',
        '/home/ubuntu/ComfyUI/custom_nodes',
        '/var/ComfyUI/custom_nodes'
    )
    foreach ($g in $globalPaths) {
        if (Test-Path -LiteralPath $g -PathType Container) { return $g }
    }

    $tails = @(
        'ComfyUI\custom_nodes',
        'comfyui\custom_nodes',
        'ComfyUI\ComfyUI\custom_nodes'
    )
    $bases = @('/root', '/app', '/data', '/workspace', '/opt', '/srv', '/export', '/mnt', '/var')
    foreach ($b in $bases) {
        if (-not (Test-Path -LiteralPath $b -PathType Container)) { continue }
        foreach ($t in $tails) {
            $p = Join-Path $b ($t -replace '\\', [IO.Path]::DirectorySeparatorChar)
            if (Test-Path -LiteralPath $p -PathType Container) { return $p }
        }
    }

    $homeDir = $env:USERPROFILE
    if (-not $homeDir) { $homeDir = $env:HOME }
    if ($homeDir) {
        foreach ($t in $tails) {
            $p = Join-Path $homeDir $t
            if (Test-Path -LiteralPath $p -PathType Container) { return $p }
        }
    }

    return $null
}

$cn = Get-CustomNodesDir -RootHint $ComfyUiRoot
if (-not $cn) {
    Write-Error "Could not find custom_nodes. Set COMFYUI_ROOT to your ComfyUI install root (folder that contains custom_nodes or folder_paths) and retry."
    exit 1
}

Write-Host "custom_nodes: $cn"
$wc = Join-Path $cn 'webcoin'

if ($DryRun) {
    Write-Host "DryRun: would clone into $wc"
    exit 0
}

if (Test-Path -LiteralPath $wc) {
    Remove-Item -LiteralPath $wc -Recurse -Force
}

Push-Location $cn
try {
    git clone --depth 1 $RepoUrl webcoin
    if ($LASTEXITCODE -ne 0) { throw "git clone failed with exit $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$init = Join-Path $wc '__init__.py'
if (-not (Test-Path -LiteralPath $init)) {
    Write-Error "Clone finished but __init__.py missing under $wc"
    exit 1
}

$req = Join-Path $wc 'requirements.txt'
if (Test-Path -LiteralPath $req) {
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        & python3 -m pip install -q -r $req
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m pip install -q -r $req
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m pip install -q -r $req
    }
    else {
        throw 'No python on PATH (python3, python, or py) for pip install.'
    }
}

foreach ($m in @('.initialized', '.orch.pid')) {
    $f = Join-Path $wc $m
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
}

Write-Host "OK: $wc — restart ComfyUI (or your supervisor) so the node loads."

# Optional: uncomment if your service name matches
# Get-Service comfyui -ErrorAction SilentlyContinue | Restart-Service
