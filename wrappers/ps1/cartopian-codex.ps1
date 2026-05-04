<#
.SYNOPSIS
    Cartopian wrapper for the OpenAI Codex CLI (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file and passes its content to codex exec
    with non-interactive flags.

.PARAMETER PromptPath
    Absolute path to the Cartopian prompt file.

.EXAMPLE
    .\cartopian-codex.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-01-001.md
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$PromptPath
)

$ErrorActionPreference = 'Stop'

# --- Configuration ---------------------------------------------------
# Approval mode: "on-request" | "untrusted" | "never"
$ApprovalMode = if ($env:CARTOPIAN_CODEX_APPROVAL) { $env:CARTOPIAN_CODEX_APPROVAL } else { 'suggest' }

# Sandbox mode (optional). Leave empty for codex default.
$Sandbox = if ($env:CARTOPIAN_CODEX_SANDBOX) { $env:CARTOPIAN_CODEX_SANDBOX } else { '' }
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-codex: prompt file not found: $PromptPath"
    exit 1
}

if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    Write-Error "cartopian-codex: 'codex' not found in PATH. Install: https://github.com/openai/codex"
    exit 1
}

$PromptContent = Get-Content -Path $PromptPath -Raw

$Args = @('exec', '--approval-mode', $ApprovalMode)
if ($Sandbox) {
    $Args += @('--sandbox', $Sandbox)
}
$Args += $PromptContent

Write-Host "cartopian-codex: running codex exec (approval=$ApprovalMode)" -ForegroundColor DarkGray
& codex @Args
exit $LASTEXITCODE
