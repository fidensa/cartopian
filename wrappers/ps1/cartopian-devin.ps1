<#
.SYNOPSIS
    Cartopian wrapper for the Devin CLI (PowerShell).

.DESCRIPTION
    Passes a Cartopian prompt file path to devin -p --prompt-file
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
# Permission mode (per current `devin --help`): 'auto' | 'dangerous'.
# Default is 'dangerous' so devin runs non-interactively, matching the
# autonomy posture of cartopian-codex, cartopian-claude, and
# cartopian-gemini. If autonomy is not desired for a given role, do
# not run that role in auto mode.
# Legacy values are accepted for backward compatibility:
#   normal -> auto
#   bypass -> dangerous
$PermissionMode = if ($env:CARTOPIAN_DEVIN_PERMISSION) { $env:CARTOPIAN_DEVIN_PERMISSION } else { 'dangerous' }
switch ($PermissionMode) {
    'normal' { $PermissionMode = 'auto' }
    'bypass' { $PermissionMode = 'dangerous' }
}
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-devin: prompt file not found: $PromptPath"
    exit 1
}

if (-not (Get-Command devin -ErrorAction SilentlyContinue)) {
    Write-Error "cartopian-devin: 'devin' not found in PATH. Install: https://docs.devin.ai/"
    exit 1
}

$PromptPathAbs = (Resolve-Path $PromptPath).Path

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
    $PromptsDir   = Split-Path -Parent $PromptPathAbs
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

$Args = @('-p', '--permission-mode', $PermissionMode, '--prompt-file', $PromptPathAbs)

# --- OS-enforced deadline (CARTOPIAN_TIMEOUT) -----------------------
# Spawn the upstream CLI as a child process and kill it deterministically
# at the configured deadline (default 60m). The PM sets CARTOPIAN_TIMEOUT
# from the resolved [handoffs.<role>].timeout; it does not poll or
# watchdog the running process. Exit code 124 signals deadline kill.
# See protocol/CONVENTIONS.md -> Handoffs.
function ConvertTo-CartopianTimeoutSeconds([string]$spec) {
    if (-not $spec) { return 3600 }
    if ($spec -match '^\s*(\d+)\s*([smhSMH]?)\s*$') {
        $n = [int]$Matches[1]
        $unit = $Matches[2].ToLower()
        if (-not $unit) { return $n * 60 }
        switch ($unit) {
            's' { return $n }
            'm' { return $n * 60 }
            'h' { return $n * 3600 }
        }
    }
    return 3600
}
$TimeoutSpec = if ($env:CARTOPIAN_TIMEOUT) { $env:CARTOPIAN_TIMEOUT } else { '60m' }
$TimeoutSec = ConvertTo-CartopianTimeoutSeconds $TimeoutSpec
# --------------------------------------------------------------------

Write-Host "cartopian-devin: running devin -p (permission=$PermissionMode, timeout=$TimeoutSpec)" -ForegroundColor DarkGray

$proc = Start-Process -FilePath devin -ArgumentList $Args -NoNewWindow -PassThru -ErrorAction Stop
if ($proc.WaitForExit($TimeoutSec * 1000)) {
    exit $proc.ExitCode
} else {
    try { $proc.Kill() } catch {}
    Write-Host "cartopian-devin: timeout after $TimeoutSpec — process killed (exit 124)" -ForegroundColor DarkYellow
    exit 124
}
