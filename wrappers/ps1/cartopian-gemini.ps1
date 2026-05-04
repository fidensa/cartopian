<#
.SYNOPSIS
    Cartopian wrapper for the Google Gemini CLI (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file and passes its content to gemini -p
    with non-interactive flags.

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
$Sandbox = if ($env:CARTOPIAN_GEMINI_SANDBOX) { $env:CARTOPIAN_GEMINI_SANDBOX } else { '' }
$AutoYes = if ($env:CARTOPIAN_GEMINI_YES -eq 'true') { $true } else { $false }
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

$Args = @('-p')
if ($AutoYes) {
    $Args += '-y'
}
if ($Sandbox) {
    $Args += @('--sandbox', $Sandbox)
}
$Args += $PromptContent

Write-Host "cartopian-gemini: running gemini -p" -ForegroundColor DarkGray
& gemini @Args
exit $LASTEXITCODE
