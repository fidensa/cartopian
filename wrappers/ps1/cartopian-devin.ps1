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

$Args = @('-p', '--permission-mode', $PermissionMode, '--', $PromptContent)

Write-Host "cartopian-devin: running devin -p (permission=$PermissionMode)" -ForegroundColor DarkGray
& devin @Args
exit $LASTEXITCODE
