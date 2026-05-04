<#
.SYNOPSIS
    Cartopian wrapper for the Claude Code CLI (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file and passes its content to claude -p
    with non-interactive flags.

.PARAMETER PromptPath
    Absolute path to the Cartopian prompt file.

.EXAMPLE
    .\cartopian-claude.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-01-001.md
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$PromptPath
)

$ErrorActionPreference = 'Stop'

# --- Configuration ---------------------------------------------------
# AllowedTools restricts which tools claude can use. Empty (default)
# means claude uses its full default tool set, which is what an
# autonomous coder/reviewer handoff needs.
$AllowedTools = if ($env:CARTOPIAN_CLAUDE_TOOLS) { $env:CARTOPIAN_CLAUDE_TOOLS } else { '' }
$OutputFormat = if ($env:CARTOPIAN_CLAUDE_FORMAT) { $env:CARTOPIAN_CLAUDE_FORMAT } else { 'text' }
$Bare = if ($env:CARTOPIAN_CLAUDE_BARE -eq 'true') { $true } else { $false }
# Skip permission prompts so claude runs non-interactively. Matches
# the autonomy posture of cartopian-codex and cartopian-gemini. Set
# CARTOPIAN_CLAUDE_SKIP_PERMS=false to re-enable prompts.
$SkipPermissions = if ($env:CARTOPIAN_CLAUDE_SKIP_PERMS -eq 'false') { $false } else { $true }
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-claude: prompt file not found: $PromptPath"
    exit 1
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Error "cartopian-claude: 'claude' not found in PATH. Install: https://docs.anthropic.com/en/docs/claude-code"
    exit 1
}

$PromptContent = Get-Content -Path $PromptPath -Raw

$Args = @('-p')
if ($AllowedTools) {
    $Args += @('--allowedTools', $AllowedTools)
}
if ($OutputFormat -ne 'text') {
    $Args += @('--output-format', $OutputFormat)
}
if ($Bare) {
    $Args += '--bare'
}
if ($SkipPermissions) {
    $Args += '--dangerously-skip-permissions'
}
$Args += $PromptContent

$TraceTools = if ($AllowedTools) { $AllowedTools } else { 'default' }
Write-Host "cartopian-claude: running claude -p (tools=$TraceTools, skip-perms=$SkipPermissions)" -ForegroundColor DarkGray
& claude @Args
exit $LASTEXITCODE
