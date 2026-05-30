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

# --- Status-file helper (early-crash signal for wait-handoff) --------
# Dot-source the shared helper that emits <report-path>.status on assignee
# exit. Optional: if the helper is missing, fall back to no-op stubs so the
# wrapper still runs (the status file is never a hard requirement).
$CartopianStatusModule = Join-Path $PSScriptRoot 'CartopianStatus.ps1'
if (Test-Path -LiteralPath $CartopianStatusModule) {
    . $CartopianStatusModule
} else {
    function Get-CartopianStatusPath { param([string]$PromptPath) return $null }
    function Write-CartopianStatus { param([string]$StatusPath, [int]$ExitCode, [bool]$TimedOut) }
}

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

# Derive the optional status-file path now, before any Set-Location, so a
# relative prompt path still resolves. $null when outside a project layout.
$StatusPath = Get-CartopianStatusPath $PromptPath

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
        Write-Error "cartopian-claude: CARTOPIAN_LAUNCH_CWD='$($env:CARTOPIAN_LAUNCH_CWD)' is not a directory"
        exit 1
    }
    $LaunchCwd = (Resolve-Path $env:CARTOPIAN_LAUNCH_CWD).Path
    Set-Location $LaunchCwd
    Write-Host "cartopian-claude: cwd=$LaunchCwd (CARTOPIAN_LAUNCH_CWD override)" -ForegroundColor DarkGray
} else {
    $PromptAbs    = (Resolve-Path $PromptPath).Path
    $PromptsDir   = Split-Path -Parent $PromptAbs
    $ProjectDir   = Split-Path -Parent $PromptsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts') {
        Set-Location $ProjectDir
        Write-Host "cartopian-claude: cwd=$ProjectDir" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-claude: prompt is outside a Cartopian project layout; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
    }
}
# --------------------------------------------------------------------

$WorkRootsJson = ''
try {
    $ResolveOut = cartopian resolve-config (Get-Location).Path 2>$null | Select-Object -First 1
    if ($ResolveOut) { $WorkRootsJson = $ResolveOut }
} catch { $WorkRootsJson = '' }
if ($WorkRootsJson) {
    # Parse tolerance ONLY: a missing/non-zero/non-JSON resolve-config (cartopian
    # absent, project not registered, ad-hoc/test layout) leaves $rec null so the
    # security guards below are skipped and the <report>.status file is still
    # emitted deterministically. The guards themselves live OUTSIDE this catch:
    # with $ErrorActionPreference = 'Stop' a guard Write-Error is a *terminating*
    # error that a surrounding empty catch would swallow before exit 1 ran,
    # defeating the fail-closed [work-root] contract (protocol/CONVENTIONS.md).
    # We therefore write the guard message to stderr explicitly and exit 1, which
    # no catch can intercept.
    $rec = $null
    try { $rec = $WorkRootsJson | ConvertFrom-Json } catch { $rec = $null }
    if ($rec) {
        $roots = @()
        if ($rec.work_roots) { $roots = $rec.work_roots.PSObject.Properties.Value }
        if ($roots.Count -gt 0) {
            foreach ($r in $roots) {
                if (-not (Test-Path -PathType Container $r)) {
                    [Console]::Error.WriteLine("[work-root] missing: $r")
                    exit 1
                }
            }
            if ($env:CARTOPIAN_CLAUDE_UNRESTRICTED -ne 'true') {
                [Console]::Error.WriteLine("[work-root] tool cannot scope multi-root access; set CARTOPIAN_CLAUDE_UNRESTRICTED=true to bypass (dangerous)")
                exit 1
            } else {
                Write-Host "cartopian-claude: unrestricted mode enabled; proceeding without scoped grants" -ForegroundColor DarkGray
            }
        }
    }
}

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

$TraceTools = if ($AllowedTools) { $AllowedTools } else { 'default' }
Write-Host "cartopian-claude: running claude -p (tools=$TraceTools, skip-perms=$SkipPermissions, timeout=$TimeoutSpec)" -ForegroundColor DarkGray

$proc = Start-Process -FilePath claude -ArgumentList $Args -NoNewWindow -PassThru -ErrorAction Stop
if ($proc.WaitForExit($TimeoutSec * 1000)) {
    Write-CartopianStatus -StatusPath $StatusPath -ExitCode $proc.ExitCode -TimedOut $false
    exit $proc.ExitCode
} else {
    try { $proc.Kill() } catch {}
    Write-Host "cartopian-claude: timeout after $TimeoutSpec — process killed (exit 124)" -ForegroundColor DarkYellow
    Write-CartopianStatus -StatusPath $StatusPath -ExitCode 124 -TimedOut $true
    exit 124
}
