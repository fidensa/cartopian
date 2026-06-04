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

    Note on git-repo check: `codex exec` refuses to run unless cwd is
    inside a git repository (or --skip-git-repo-check is passed). The
    wrapper passes --skip-git-repo-check unconditionally; sandbox safety
    still comes from --sandbox workspace-write and does not depend on
    cwd being a git repo.

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
        Write-Error "cartopian-codex: CARTOPIAN_LAUNCH_CWD='$($env:CARTOPIAN_LAUNCH_CWD)' is not a directory"
        exit 1
    }
    $LaunchCwd = (Resolve-Path $env:CARTOPIAN_LAUNCH_CWD).Path
    Set-Location $LaunchCwd
    Write-Host "cartopian-codex: cwd=$LaunchCwd (CARTOPIAN_LAUNCH_CWD override)" -ForegroundColor DarkGray
} else {
    $PromptAbs    = (Resolve-Path $PromptPath).Path
    $PromptsDir   = Split-Path -Parent $PromptAbs
    $ProjectDir   = Split-Path -Parent $PromptsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts') {
        Set-Location $ProjectDir
        Write-Host "cartopian-codex: cwd=$ProjectDir" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-codex: prompt is outside a Cartopian project layout; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
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
            if ($env:CARTOPIAN_CODEX_UNRESTRICTED -ne 'true') {
                [Console]::Error.WriteLine("[work-root] tool cannot scope multi-root access; set CARTOPIAN_CODEX_UNRESTRICTED=true to bypass (dangerous)")
                exit 1
            } else {
                Write-Host "cartopian-codex: unrestricted mode enabled; proceeding without scoped grants" -ForegroundColor DarkGray
            }
        }
    }
}

$Args = @('exec', '--skip-git-repo-check')
# Agent-neutral model selection: dispatch exports CARTOPIAN_MODEL from the
# resolved [handoffs.<role>].model; translate it into codex's --model flag.
# Unset means codex's own default model.
if ($env:CARTOPIAN_MODEL) {
    $Args += @('--model', $env:CARTOPIAN_MODEL)
}
if ($Bypass) {
    $Args += '--dangerously-bypass-approvals-and-sandbox'
} elseif ($Sandbox) {
    $Args += @('--sandbox', $Sandbox)
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

if ($Bypass) {
    Write-Host "cartopian-codex: running codex exec (bypass=on, sandbox=disabled, skip-git-repo-check=on, timeout=$TimeoutSpec)" -ForegroundColor DarkGray
} else {
    Write-Host "cartopian-codex: running codex exec (sandbox=$Sandbox, skip-git-repo-check=on, timeout=$TimeoutSpec)" -ForegroundColor DarkGray
}

$proc = Start-Process -FilePath codex -ArgumentList $Args -NoNewWindow -PassThru -ErrorAction Stop
if ($proc.WaitForExit($TimeoutSec * 1000)) {
    Write-CartopianStatus -StatusPath $StatusPath -ExitCode $proc.ExitCode -TimedOut $false
    exit $proc.ExitCode
} else {
    try { $proc.Kill() } catch {}
    Write-Host "cartopian-codex: timeout after $TimeoutSpec — process killed (exit 124)" -ForegroundColor DarkYellow
    Write-CartopianStatus -StatusPath $StatusPath -ExitCode 124 -TimedOut $true
    exit 124
}
