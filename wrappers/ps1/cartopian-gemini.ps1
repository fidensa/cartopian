<#
.SYNOPSIS
    Cartopian wrapper for the Google Gemini CLI (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file and passes its content to gemini -p
    with non-interactive flags.

    gemini's `--sandbox` flag is boolean (presence-only), not a value
    flag. Autonomy is controlled via `--approval-mode` (default,
    auto_edit, yolo, plan) or the legacy `-y/--yolo` boolean.

.PARAMETER PromptPath
    Absolute path to the Cartopian prompt file.

.EXAMPLE
    .\cartopian-gemini.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-01-001.md
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$PromptPath
)

$ErrorActionPreference = 'Stop'

# --- Configuration ---------------------------------------------------
# Approval mode: 'default' | 'auto_edit' | 'yolo' | 'plan'.
# Empty string falls back to the legacy --yolo / -y mechanism below.
$ApprovalMode = if ($env:CARTOPIAN_GEMINI_APPROVAL) { $env:CARTOPIAN_GEMINI_APPROVAL } else { 'yolo' }

# Legacy YOLO toggle (used only if $ApprovalMode is empty).
$AutoYes = if ($env:CARTOPIAN_GEMINI_YES -eq 'true') { $true } else { $false }

# Sandbox toggle (boolean flag).
$Sandbox = if ($env:CARTOPIAN_GEMINI_SANDBOX -eq 'true') { $true } else { $false }
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-gemini: prompt file not found: $PromptPath"
    exit 1
}

if (-not (Get-Command gemini -ErrorAction SilentlyContinue)) {
    Write-Error "cartopian-gemini: 'gemini' not found in PATH. Install: https://github.com/google-gemini/gemini-cli"
    exit 1
}

$PromptContent = Get-Content -Path $PromptPath -Raw

# --- Launch directory ------------------------------------------------
# FR-012: assignee CLIs run with cwd set to the Cartopian project root
# (the registered project path). Prompts always live at
# <workspace>/projects/<project-id>/prompts/PROMPT-*.md, so the project
# root is derivable from the prompt path alone.
#
# Override: set CARTOPIAN_LAUNCH_CWD to an absolute or relative path to
# skip auto-resolution. Useful for split-layout, cross-drive, monorepo,
# or per-repo-sandbox setups. A non-existent path is a hard error, not
# a silent fallback.
if ($env:CARTOPIAN_LAUNCH_CWD) {
    if (-not (Test-Path -PathType Container $env:CARTOPIAN_LAUNCH_CWD)) {
        Write-Error "cartopian-gemini: CARTOPIAN_LAUNCH_CWD='$($env:CARTOPIAN_LAUNCH_CWD)' is not a directory"
        exit 1
    }
    $LaunchCwd = (Resolve-Path $env:CARTOPIAN_LAUNCH_CWD).Path
    Set-Location $LaunchCwd
    Write-Host "cartopian-gemini: cwd=$LaunchCwd (CARTOPIAN_LAUNCH_CWD override)" -ForegroundColor DarkGray
} else {
    $PromptAbs    = (Resolve-Path $PromptPath).Path
    $PromptsDir   = Split-Path -Parent $PromptAbs
    $ProjectDir   = Split-Path -Parent $PromptsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts') {
        Set-Location $ProjectDir
        Write-Host "cartopian-gemini: cwd=$ProjectDir" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-gemini: prompt is outside a Cartopian project layout; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
    }
}
# --------------------------------------------------------------------

# --- Access grants (OQ-009) -----------------------------------------
# Read resolved work-root absolute paths via Core CLI. Fail-closed when
# non-empty and per-tool sandbox cannot scope multi-root access. Allow an
# explicit per-invocation bypass via CARTOPIAN_GEMINI_UNRESTRICTED=true.
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
            if ($env:CARTOPIAN_GEMINI_UNRESTRICTED -ne 'true') {
                Write-Error "[work-root] tool cannot scope multi-root access; set CARTOPIAN_GEMINI_UNRESTRICTED=true to bypass (dangerous)"
                exit 1
            } else {
                Write-Host "cartopian-gemini: unrestricted mode enabled; proceeding without scoped grants" -ForegroundColor DarkGray
            }
        }
    } catch {}
}
# --------------------------------------------------------------------

$Args = @()
if ($ApprovalMode) {
    $Args += @('--approval-mode', $ApprovalMode)
} elseif ($AutoYes) {
    $Args += '-y'
}
if ($Sandbox) {
    $Args += '--sandbox'
}
$Args += @('-p', $PromptContent)

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

if ($ApprovalMode) {
    Write-Host "cartopian-gemini: running gemini -p (approval=$ApprovalMode, timeout=$TimeoutSpec)" -ForegroundColor DarkGray
} else {
    Write-Host "cartopian-gemini: running gemini -p (yolo=$AutoYes, timeout=$TimeoutSpec)" -ForegroundColor DarkGray
}

$proc = Start-Process -FilePath gemini -ArgumentList $Args -NoNewWindow -PassThru -ErrorAction Stop
if ($proc.WaitForExit($TimeoutSec * 1000)) {
    exit $proc.ExitCode
} else {
    try { $proc.Kill() } catch {}
    Write-Host "cartopian-gemini: timeout after $TimeoutSpec — process killed (exit 124)" -ForegroundColor DarkYellow
    exit 124
}
