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
# Cartopian convention: assignee CLIs run with cwd set to the parent of
# the workspace root, so the assignee's filesystem access spans both the
# protocol repo (for report write-back under .../projects/<proj>/reports/)
# and the sibling target product repo named in the task's `Repo subpath:`
# field. Prompts always live at <workspace>/projects/<proj>/prompts/
# PROMPT-*.md, so the launch cwd is derivable from the prompt path
# alone.
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
    $ProjectsDir  = Split-Path -Parent $ProjectDir
    $WorkspaceRoot = Split-Path -Parent $ProjectsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts' -and `
        (Split-Path -Leaf $ProjectsDir) -eq 'projects') {
        $LaunchCwd = Split-Path -Parent $WorkspaceRoot
        Set-Location $LaunchCwd
        Write-Host "cartopian-gemini: cwd=$LaunchCwd" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-gemini: prompt is outside a Cartopian workspace; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
    }
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

if ($ApprovalMode) {
    Write-Host "cartopian-gemini: running gemini -p (approval=$ApprovalMode)" -ForegroundColor DarkGray
} else {
    Write-Host "cartopian-gemini: running gemini -p (yolo=$AutoYes)" -ForegroundColor DarkGray
}
& gemini @Args
exit $LASTEXITCODE
