<#
.SYNOPSIS
    Cartopian wrapper for the Hermes CLI agent (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file path and passes it to Hermes one-shot
    mode (`hermes -z`) with non-interactive flags. Verified against
    Hermes Agent v0.20.0 (2026.8.3) on macOS; behavior on native Windows
    is UNVERIFIED (the port is mechanical and statically tested).

    Note on one-shot mode: `hermes -z` prints only the final response and
    auto-bypasses every interactive surface (clarify prompts, shell-hook
    approval, dangerous-command approval). No wrapper flag is needed to
    prevent hangs — and none can restore approvals in one-shot mode.

    Note on permissions: one-shot mode has no approval layer at all and
    Hermes's write guards are documented as bypassable via the terminal
    tool, so this wrapper deliberately injects NO permission policy: one
    would look like a write boundary while being sidestepped by design.

    Note on profiles: Hermes profile selection is the -p/--profile flag
    (pre-parsed before imports). Exporting HERMES_PROFILE selects
    nothing, so CARTOPIAN_HERMES_PROFILE is translated into the flag.

.PARAMETER PromptPath
    Absolute path to the Cartopian prompt file.

.EXAMPLE
    .\cartopian-hermes.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-NN-NNN.md
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
# CARTOPIAN_HERMES_PROVIDER   -> --provider (optional; -m accepts provider/model compounds)
# CARTOPIAN_HERMES_PROFILE    -> -p/--profile flag (the only selection mechanism)
# CARTOPIAN_HERMES_USAGE_FILE -> --usage-file (opt-in spend evidence; written even on failure)
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-hermes: prompt file not found: $PromptPath"
    exit 1
}

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) {
    Write-Error "cartopian-hermes: 'hermes' not found in PATH. Install: https://hermes-agent.nousresearch.com"
    exit 1
}

# Hand the agent the prompt FILE PATH, not the file's text. Embedding a
# multi-KB markdown body as a command-line argument mangles under PowerShell
# argument parsing; the agent opens the file itself.
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
        Write-Error "cartopian-hermes: CARTOPIAN_LAUNCH_CWD='$($env:CARTOPIAN_LAUNCH_CWD)' is not a directory"
        exit 1
    }
    $LaunchCwd = (Resolve-Path $env:CARTOPIAN_LAUNCH_CWD).Path
    Set-Location $LaunchCwd
    Write-Host "cartopian-hermes: cwd=$LaunchCwd (CARTOPIAN_LAUNCH_CWD override)" -ForegroundColor DarkGray
} else {
    $PromptAbs    = (Resolve-Path $PromptPath).Path
    $PromptsDir   = Split-Path -Parent $PromptAbs
    $ProjectDir   = Split-Path -Parent $PromptsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts') {
        Set-Location $ProjectDir
        Write-Host "cartopian-hermes: cwd=$ProjectDir" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-hermes: prompt is outside a Cartopian project layout; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
    }
}
# --------------------------------------------------------------------

$Args = @()

# Profile selection is the -p flag, pre-parsed by Hermes before imports.
# Exporting HERMES_PROFILE selects nothing (it is Kanban author attribution).
if ($env:CARTOPIAN_HERMES_PROFILE) {
    $Args += @('-p', $env:CARTOPIAN_HERMES_PROFILE)
}

# One-shot mode: pass the prompt file PATH as the one-shot prompt; Hermes
# loads AGENTS.md from the launch cwd and reads the file with its own tools.
$Args += @('-z', $PromptPathAbs)

# Agent-neutral model selection: dispatch exports CARTOPIAN_MODEL from the
# resolved dispatch model; translate it into Hermes's -m flag verbatim
# (Hermes accepts provider/model compounds without --provider).
if ($env:CARTOPIAN_MODEL) {
    $Args += @('-m', $env:CARTOPIAN_MODEL)
}

# Optional provider disambiguation for bare model ids.
if ($env:CARTOPIAN_HERMES_PROVIDER) {
    $Args += @('--provider', $env:CARTOPIAN_HERMES_PROVIDER)
}

# Agent-neutral effort selection: dispatch exports CARTOPIAN_EFFORT from the
# resolved dispatch effort; translate it into Hermes's --reasoning flag over
# Hermes's own closed vocabulary. Values outside it fall back to the default
# effort (warn + omit). The vocabulary tracks the installed Hermes CLI
# generation and may drift as it evolves.
if ($env:CARTOPIAN_EFFORT) {
    $EffortLc = $env:CARTOPIAN_EFFORT.ToLowerInvariant()
    if ($EffortLc -in @('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')) {
        $Args += @('--reasoning', $EffortLc)
    } else {
        [Console]::Error.WriteLine("cartopian-hermes: CARTOPIAN_EFFORT=$($env:CARTOPIAN_EFFORT) is not a supported hermes reasoning level (none|minimal|low|medium|high|xhigh|max|ultra); launching with the default effort")
    }
}

# Opt-in spend evidence: Hermes writes a JSON cost/token report to this path,
# even on failure.
if ($env:CARTOPIAN_HERMES_USAGE_FILE) {
    $Args += @('--usage-file', $env:CARTOPIAN_HERMES_USAGE_FILE)
}

# No permission-policy injection: one-shot mode has no approval layer, and a
# wrapper-set write guard would misrepresent containment (see header).

# Work-root no-op notice: Hermes imposes no default path sandbox, so declared
# work roots need no grant here. Actual access remains subject to OS
# permissions and any operator-configured Hermes guards.
if ($env:CARTOPIAN_WORK_ROOTS) {
    [Console]::Error.WriteLine("cartopian-hermes: hermes has no default path sandbox; work roots need no grant here, and actual access remains subject to OS permissions and any operator-configured hermes guards (CARTOPIAN_WORK_ROOTS=$($env:CARTOPIAN_WORK_ROOTS))")
}

# --- OS-enforced deadline (CARTOPIAN_TIMEOUT) -----------------------
# Spawn the upstream CLI as a child process and kill it deterministically
# at the configured deadline (default 60m). The PM sets CARTOPIAN_TIMEOUT
# from the resolved dispatch timeout; it does not poll or
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

Write-Host "cartopian-hermes: running hermes -z (timeout=$TimeoutSpec)" -ForegroundColor DarkGray

# Run under the report-completion supervisor (parity with the bash
# cartopian_run_supervised): once the authoritative report file appears, a
# lingering child is reaped promptly so a finished handoff exits 0/clean
# instead of idling to the CARTOPIAN_TIMEOUT deadline. The deadline (the
# single SSOT timer, enforced inside the supervisor) is untouched -- a genuine
# hang that writes no report still hits it (exit 124). The watched report path
# is the status path without its ".status" suffix (shared derivation --
# Get-CartopianReportPath in CartopianStatus.ps1 owns the suffix contract).
# Hermes's own exit contract (0 success / 1 agent failure / 2 failed-or-partial
# with no text) needs no mapping: any nonzero exit records as failure.
$ReportPath = Get-CartopianReportPath $StatusPath

$run = Invoke-CartopianSupervisedRun -ReportPath $ReportPath -FilePath hermes -ArgumentList $Args -TimeoutSec $TimeoutSec
if ($run.TimedOut) {
    Write-Host "cartopian-hermes: timeout after $TimeoutSpec -- process killed (exit 124)" -ForegroundColor DarkYellow
}
Write-CartopianStatus -StatusPath $StatusPath -ExitCode $run.ExitCode -TimedOut $run.TimedOut
exit $run.ExitCode
