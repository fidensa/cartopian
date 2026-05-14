<#
.SYNOPSIS
    Cartopian wrapper for the Devin CLI (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file and passes its content to devin -p
    with non-interactive flags.

.PARAMETER PromptPath
    Absolute path to the Cartopian prompt file.

.EXAMPLE
    .\cartopian-devin.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-01-001.md
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$PromptPath
)

$ErrorActionPreference = 'Stop'

# --- Configuration ---------------------------------------------------
# Permission mode (per `devin --help`): 'normal' | 'dangerous' | 'bypass'.
# Default is 'bypass' so devin runs non-interactively, matching the
# autonomy posture of cartopian-codex, cartopian-claude, and
# cartopian-gemini. If autonomy is not desired for a given role, do
# not run that role in auto mode.
# 'accept-edits', 'plan', and 'autonomous' are interactive slash commands
# inside a session, not values for the --permission-mode flag.
$PermissionMode = if ($env:CARTOPIAN_DEVIN_PERMISSION) { $env:CARTOPIAN_DEVIN_PERMISSION } else { 'bypass' }
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-devin: prompt file not found: $PromptPath"
    exit 1
}

if (-not (Get-Command devin -ErrorAction SilentlyContinue)) {
    Write-Error "cartopian-devin: 'devin' not found in PATH. Install: https://docs.devin.ai/"
    exit 1
}

$PromptContent = Get-Content -Path $PromptPath -Raw

# --- Launch directory ------------------------------------------------
# FR-012: assignee CLIs run with cwd set to the Cartopian project root
# (the registered project path). Prompts always live at
# <workspace>/projects/<project-id>/prompts/PROMPT-*.md, so the project
# root is derivable from the prompt path alone.
if ($env:CARTOPIAN_LAUNCH_CWD) {
    if (-not (Test-Path -PathType Container $env:CARTOPIAN_LAUNCH_CWD)) {
        Write-Error "cartopian-devin: CARTOPIAN_LAUNCH_CWD='$($env:CARTOPIAN_LAUNCH_CWD)' is not a directory"
        exit 1
    }
    $LaunchCwd = (Resolve-Path $env:CARTOPIAN_LAUNCH_CWD).Path
    Set-Location $LaunchCwd
    Write-Host "cartopian-devin: cwd=$LaunchCwd (CARTOPIAN_LAUNCH_CWD override)" -ForegroundColor DarkGray
} else {
    $PromptAbs    = (Resolve-Path $PromptPath).Path
    $PromptsDir   = Split-Path -Parent $PromptAbs
    $ProjectDir   = Split-Path -Parent $PromptsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts') {
        Set-Location $ProjectDir
        Write-Host "cartopian-devin: cwd=$ProjectDir" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-devin: prompt is outside a Cartopian project layout; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
    }
}
# --------------------------------------------------------------------

# --- Access grants (OQ-009) -----------------------------------------
# Read resolved work-root absolute paths via Core CLI. Fail-closed when
# non-empty and per-tool sandbox cannot scope multi-root access. Allow an
# explicit per-invocation bypass via CARTOPIAN_DEVIN_UNRESTRICTED=true.
$WorkRootsJson = ''
$ResolveOut = cartopian resolve-config (Get-Location).Path | Select-Object -First 1
if ($LASTEXITCODE -ne 0) {
    Write-Error "[work-root] resolve-config failed for $(Get-Location).Path"
    exit 1
}
if ($ResolveOut) { $WorkRootsJson = $ResolveOut }
if ($WorkRootsJson) {
    try {
        $rec = $WorkRootsJson | ConvertFrom-Json
        $roots = @()
        if ($rec.work_roots) { $roots = $rec.work_roots.PSObject.Properties.Value }
        if ($roots.Count -gt 0) {
            foreach ($r in $roots) {
                if (-not (Test-Path -PathType Container $r)) {
                    Write-Error "[work-root] missing: $r"
                    exit 1
                }
            }
            if ($env:CARTOPIAN_DEVIN_UNRESTRICTED -ne 'true') {
                Write-Error "[work-root] tool cannot scope multi-root access; set CARTOPIAN_DEVIN_UNRESTRICTED=true to bypass (dangerous)"
                exit 1
            } else {
                Write-Host "cartopian-devin: unrestricted mode enabled; proceeding without scoped grants" -ForegroundColor DarkGray
            }
        }
    } catch {}
}
# --------------------------------------------------------------------

$Args = @('-p', '--permission-mode', $PermissionMode, '--', $PromptContent)

Write-Host "cartopian-devin: running devin -p (permission=$PermissionMode)" -ForegroundColor DarkGray
& devin @Args
exit $LASTEXITCODE
