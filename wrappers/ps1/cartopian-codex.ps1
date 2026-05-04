<#
.SYNOPSIS
    Cartopian wrapper for the OpenAI Codex CLI (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file and passes its content to codex exec
    with non-interactive flags.

    Note: `codex exec` is non-interactive and has no --approval-mode /
    --ask-for-approval flag (those live on the interactive `codex`
    command). Autonomy in exec mode is controlled by the sandbox scope
    plus --dangerously-bypass-approvals-and-sandbox. Verified against
    codex-cli 0.128.0.

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
# Sandbox scope: 'read-only' | 'workspace-write' | 'danger-full-access'
$Sandbox = if ($env:CARTOPIAN_CODEX_SANDBOX) { $env:CARTOPIAN_CODEX_SANDBOX } else { 'workspace-write' }

# Bypass all approval gates AND the sandbox. EXTREMELY DANGEROUS.
# Only enable inside externally-sandboxed environments.
$Bypass = if ($env:CARTOPIAN_CODEX_BYPASS -eq 'true') { $true } else { $false }
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

$Args = @('exec')
if ($Bypass) {
    $Args += '--dangerously-bypass-approvals-and-sandbox'
} elseif ($Sandbox) {
    $Args += @('--sandbox', $Sandbox)
}
$Args += $PromptContent

if ($Bypass) {
    Write-Host "cartopian-codex: running codex exec (bypass=on, sandbox=disabled)" -ForegroundColor DarkGray
} else {
    Write-Host "cartopian-codex: running codex exec (sandbox=$Sandbox)" -ForegroundColor DarkGray
}
& codex @Args
exit $LASTEXITCODE
