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
