<#
.SYNOPSIS
    Cartopian wrapper for the Google Antigravity CLI (agy) (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file and passes its path to agy -p
    with non-interactive flags.

    Autonomy is controlled via `--dangerously-skip-permissions` (default)
    or an explicit `--mode` (accept-edits, plan). agy's `--sandbox` flag
    is boolean (presence-only), not a value flag.

    Assignee-surface counterpart of the Antigravity PM-host entries
    (`antigravity-tui` / `antigravity-ide` in the containment matrix).

.PARAMETER PromptPath
    Absolute path to the Cartopian prompt file.

.EXAMPLE
    .\cartopian-agy.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-NN-NNN.md
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
# Execution mode: '' (agy default) | 'accept-edits' | 'plan'.
# Autonomy normally comes from the skip-permissions toggle below.
$Mode = if ($env:CARTOPIAN_AGY_MODE) { $env:CARTOPIAN_AGY_MODE } else { '' }

# Skip all permission prompts so agy runs non-interactively (matches the
# autonomy posture of the other shipped wrappers). Set the env var to
# 'false' to re-enable prompts (only useful for interactive debugging).
$SkipPermissions = if ($env:CARTOPIAN_AGY_SKIP_PERMS -eq 'false') { $false } else { $true }

# Sandbox toggle (boolean flag).
$Sandbox = if ($env:CARTOPIAN_AGY_SANDBOX -eq 'true') { $true } else { $false }
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-agy: prompt file not found: $PromptPath"
    exit 1
}

if (-not (Get-Command agy -ErrorAction SilentlyContinue)) {
    Write-Error "cartopian-agy: 'agy' not found in PATH. Install: https://antigravity.google/docs/cli/install"
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
        Write-Error "cartopian-agy: CARTOPIAN_LAUNCH_CWD='$($env:CARTOPIAN_LAUNCH_CWD)' is not a directory"
        exit 1
    }
    $LaunchCwd = (Resolve-Path $env:CARTOPIAN_LAUNCH_CWD).Path
    Set-Location $LaunchCwd
    Write-Host "cartopian-agy: cwd=$LaunchCwd (CARTOPIAN_LAUNCH_CWD override)" -ForegroundColor DarkGray
} else {
    $PromptAbs    = (Resolve-Path $PromptPath).Path
    $PromptsDir   = Split-Path -Parent $PromptAbs
    $ProjectDir   = Split-Path -Parent $PromptsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts') {
        Set-Location $ProjectDir
        Write-Host "cartopian-agy: cwd=$ProjectDir" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-agy: prompt is outside a Cartopian project layout; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
    }
}
# --------------------------------------------------------------------

# --disable-slash-commands is unconditional: agy print mode expands a
# leading "/" as a slash command or skill, and the -p value here is always
# an absolute path — it must reach the agent as literal text.
$Args = @('--disable-slash-commands')
if ($Mode) {
    $Args += @('--mode', $Mode)
}
if ($SkipPermissions) {
    $Args += '--dangerously-skip-permissions'
}
if ($Sandbox) {
    $Args += '--sandbox'
}
# Agent-neutral model selection: dispatch exports CARTOPIAN_MODEL from the
# resolved dispatch model; translate it into agy's --model flag.
# Unset means agy's own default model.
if ($env:CARTOPIAN_MODEL) {
    $Args += @('--model', $env:CARTOPIAN_MODEL)
}
# Agent-neutral effort selection: dispatch exports CARTOPIAN_EFFORT from the
# resolved dispatch effort; translate it into agy's --effort flag over agy's
# own closed vocabulary. Values outside it fall back to the default effort
# (warn + omit). The vocabulary tracks the installed agy CLI generation and
# may drift as it evolves.
#
# Most agy model ids already encode an effort level as a -low/-medium/-high
# suffix, and agy hard-fails a --model/--effort combination that conflicts.
# When the pinned model carries such a suffix the pin wins: --effort is
# dropped with a notice instead of failing the launch.
if ($env:CARTOPIAN_EFFORT) {
    $EffortLc = $env:CARTOPIAN_EFFORT.ToLowerInvariant()
    if ($EffortLc -in @('low', 'medium', 'high')) {
        if ($env:CARTOPIAN_MODEL -match '-(low|medium|high)$') {
            [Console]::Error.WriteLine("cartopian-agy: model '$($env:CARTOPIAN_MODEL)' already encodes an effort level; ignoring CARTOPIAN_EFFORT=$($env:CARTOPIAN_EFFORT)")
        } else {
            $Args += @('--effort', $EffortLc)
        }
    } else {
        [Console]::Error.WriteLine("cartopian-agy: CARTOPIAN_EFFORT=$($env:CARTOPIAN_EFFORT) is not a supported agy effort level (low|medium|high); launching with the default effort")
    }
}
$Args += @('-p', $PromptPathAbs)

# Work-root grant: dispatch exports CARTOPIAN_WORK_ROOTS (an os.pathsep-joined
# list — ';' on Windows — of the project's resolved work-root absolute paths).
# Declared work roots become additional workspace directories (--add-dir) so
# access there is an explicit grant, not a side effect of
# --dangerously-skip-permissions.
if ($env:CARTOPIAN_WORK_ROOTS) {
    foreach ($root in $env:CARTOPIAN_WORK_ROOTS -split ';') {
        if ($root) { $Args += @('--add-dir', $root) }
    }
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

# agy print mode carries its own internal wait timer (--print-timeout,
# default 5m) that would preempt the Cartopian deadline on any longer
# handoff. Raise it to the same duration so the supervisor's deadline
# stays the single SSOT timer (its clock starts first, so it always wins
# the tie). agy parses Go durations, which require a unit — pass the
# already-normalized seconds value.
$Args += @('--print-timeout', "${TimeoutSec}s")

Write-Host "cartopian-agy: running agy -p (skip-perms=$SkipPermissions, timeout=$TimeoutSpec)" -ForegroundColor DarkGray

# Run under the report-completion supervisor (parity with the bash
# cartopian_run_supervised): once the authoritative report file appears, a
# lingering child is reaped promptly so a finished handoff exits 0/clean
# instead of idling to the CARTOPIAN_TIMEOUT deadline. The deadline (the
# single SSOT timer, enforced inside the supervisor) is untouched -- a genuine
# hang that writes no report still hits it (exit 124). The watched report path
# is the status path without its ".status" suffix (shared derivation --
# Get-CartopianReportPath in CartopianStatus.ps1 owns the suffix contract).
$ReportPath = Get-CartopianReportPath $StatusPath

$run = Invoke-CartopianSupervisedRun -ReportPath $ReportPath -FilePath agy -ArgumentList $Args -TimeoutSec $TimeoutSec
if ($run.TimedOut) {
    Write-Host "cartopian-agy: timeout after $TimeoutSpec -- process killed (exit 124)" -ForegroundColor DarkYellow
}
Write-CartopianStatus -StatusPath $StatusPath -ExitCode $run.ExitCode -TimedOut $run.TimedOut
exit $run.ExitCode
