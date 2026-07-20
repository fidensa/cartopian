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
    .\cartopian-codex.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-NN-NNN.md
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
    # Helper absent: degrade to the historical unsupervised run (deadline only;
    # no report path to watch without the helper's derivation).
    function Get-CartopianReportPath { param([string]$StatusPath) return $null }
    function Invoke-CartopianSupervisedRun {
        param([AllowEmptyString()][AllowNull()][string]$ReportPath,
              [string]$FilePath, [object[]]$ArgumentList, [int]$TimeoutSec)
        $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -NoNewWindow -PassThru -ErrorAction Stop
        if ($proc.WaitForExit($TimeoutSec * 1000)) {
            return @{ ExitCode = $proc.ExitCode; TimedOut = $false }
        }
        try { $proc.Kill() } catch {}
        return @{ ExitCode = 124; TimedOut = $true }
    }
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

# Hand the agent the prompt FILE PATH, not the file's text. Embedding a
# multi-KB markdown body as a command-line argument mangles under PowerShell
# argument parsing; the agent opens the file itself (its directory is granted
# read access in the scope args below).
$PromptPathAbs = (Resolve-Path -LiteralPath $PromptPath).Path

# Derive the optional status-file path now, before any Set-Location, so a
# relative prompt path still resolves. $null when outside a project layout.
$StatusPath = Get-CartopianStatusPath $PromptPath

# --- Launch directory ------------------------------------------------
# Assignee CLIs run with cwd set to the Cartopian project root
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

$Args = @('exec', '--skip-git-repo-check')
# Agent-neutral model selection: dispatch exports CARTOPIAN_MODEL from the
# resolved [handoffs.<role>].model; translate it into codex's --model flag.
# Unset means codex's own default model.
if ($env:CARTOPIAN_MODEL) {
    $Args += @('--model', $env:CARTOPIAN_MODEL)
}
# Agent-neutral effort selection: dispatch exports CARTOPIAN_EFFORT from the
# resolved [handoffs.<role>].effort; translate it into codex's reasoning-effort
# config override (-c model_reasoning_effort=<level>). Values outside codex's
# CLI-wide effort vocabulary fall back to the default effort (warn + omit).
# A vocabulary-valid level a specific model rejects is passed through — that
# outcome is the tool's own behavior. The vocabulary tracks the installed
# codex CLI generation and may drift as it evolves.
if ($env:CARTOPIAN_EFFORT) {
    $EffortLc = $env:CARTOPIAN_EFFORT.ToLowerInvariant()
    if ($EffortLc -in @('low', 'medium', 'high', 'xhigh', 'max', 'ultra')) {
        $Args += @('-c', "model_reasoning_effort=$EffortLc")
    } else {
        [Console]::Error.WriteLine("cartopian-codex: CARTOPIAN_EFFORT=$($env:CARTOPIAN_EFFORT) is not a supported codex effort level (low|medium|high|xhigh|max|ultra); launching with the default effort")
    }
}
if ($Bypass) {
    $Args += '--dangerously-bypass-approvals-and-sandbox'
} elseif ($Sandbox) {
    $Args += @('--sandbox', $Sandbox)
}
# Work-root sandbox widening: dispatch exports CARTOPIAN_WORK_ROOTS (a
# pathsep-joined list — ';' on Windows — of the project's resolved work-root
# absolute paths). --sandbox workspace-write roots writes at the launch cwd
# only, so without this every write into a declared work root fails.
# writable_roots is additive — the cwd workspace and temp dirs stay writable.
# Paths are TOML-escaped (backslash, double-quote) into the array literal.
if (-not $Bypass -and $Sandbox -eq 'workspace-write' -and $env:CARTOPIAN_WORK_ROOTS) {
    $WritableRoots = @()
    foreach ($root in ($env:CARTOPIAN_WORK_ROOTS -split [IO.Path]::PathSeparator)) {
        if (-not $root) { continue }
        $esc = $root.Replace('\', '\\').Replace('"', '\"')
        $WritableRoots += ('"' + $esc + '"')
    }
    if ($WritableRoots.Count -gt 0) {
        $Args += @('-c', ('sandbox_workspace_write.writable_roots=[' + ($WritableRoots -join ', ') + ']'))
        Write-Host "cartopian-codex: sandbox writable roots += $($env:CARTOPIAN_WORK_ROOTS)" -ForegroundColor DarkGray
    }
}
$Args += $PromptPathAbs

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

# Run under the report-completion supervisor (parity with the bash
# cartopian_run_supervised): once the authoritative report file appears, a
# lingering child is reaped promptly so a finished handoff exits 0/clean
# instead of idling to the CARTOPIAN_TIMEOUT deadline. The deadline (the
# single SSOT timer, enforced inside the supervisor) is untouched -- a genuine
# hang that writes no report still hits it (exit 124). The watched report path
# is the status path without its ".status" suffix (shared derivation --
# Get-CartopianReportPath in CartopianStatus.ps1 owns the suffix contract).
$ReportPath = Get-CartopianReportPath $StatusPath

$run = Invoke-CartopianSupervisedRun -ReportPath $ReportPath -FilePath codex -ArgumentList $Args -TimeoutSec $TimeoutSec
if ($run.TimedOut) {
    Write-Host "cartopian-codex: timeout after $TimeoutSpec -- process killed (exit 124)" -ForegroundColor DarkYellow
}
Write-CartopianStatus -StatusPath $StatusPath -ExitCode $run.ExitCode -TimedOut $run.TimedOut
exit $run.ExitCode
