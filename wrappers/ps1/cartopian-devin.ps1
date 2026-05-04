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
# Cartopian convention: assignee CLIs run with cwd set to the parent of
# the workspace root, so the assignee's filesystem access spans both the
# protocol repo (for report write-back under .../projects/<proj>/reports/)
# and the sibling target product repo named in the task's `Target repo:`
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
    $ProjectsDir  = Split-Path -Parent $ProjectDir
    $WorkspaceRoot = Split-Path -Parent $ProjectsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts' -and `
        (Split-Path -Leaf $ProjectsDir) -eq 'projects') {
        $LaunchCwd = Split-Path -Parent $WorkspaceRoot
        Set-Location $LaunchCwd
        Write-Host "cartopian-devin: cwd=$LaunchCwd" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-devin: prompt is outside a Cartopian workspace; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
    }
}
# --------------------------------------------------------------------

$Args = @('-p', '--permission-mode', $PermissionMode, '--', $PromptContent)

Write-Host "cartopian-devin: running devin -p (permission=$PermissionMode)" -ForegroundColor DarkGray
& devin @Args
exit $LASTEXITCODE
