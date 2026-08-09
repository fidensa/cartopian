<#
.SYNOPSIS
    Cartopian wrapper for the opencode CLI/TUI agent (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file path and passes it to `opencode run`
    with non-interactive flags. Verified against opencode 1.18.15 on
    macOS; behavior on native Windows is UNVERIFIED (the port is
    mechanical and statically tested).

    Note on git-repo check: `opencode run` does not require the cwd to
    be inside a git repository, so no skip flag is needed.

    Note on effort: --variant is opencode's provider-specific
    reasoning-effort selector. The CLI does not validate the value and
    does not persist it; a value a specific model does not offer is
    passed through. The vocabulary may drift as upstream evolves.

    Note on permissions: opencode has no filesystem sandbox — only
    allow/ask/deny rules — and an `edit` deny does not cover shell
    writes. This wrapper deliberately injects NO permission policy: one
    would look like a write boundary while being one shell redirect away
    from bypass.

.PARAMETER PromptPath
    Absolute path to the Cartopian prompt file.

.EXAMPLE
    .\cartopian-opencode.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-NN-NNN.md
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
# --auto bypasses configured `ask` permission rules, which would otherwise
# auto-reject in a non-interactive run. Set CARTOPIAN_OPENCODE_AUTO=false to
# preserve that ask behavior. Explicit deny rules and OS permissions apply
# either way.
$OpencodeAuto = if ($env:CARTOPIAN_OPENCODE_AUTO -eq 'false') { $false } else { $true }
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-opencode: prompt file not found: $PromptPath"
    exit 1
}

if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    Write-Error "cartopian-opencode: 'opencode' not found in PATH. Install: https://opencode.ai"
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
        Write-Error "cartopian-opencode: CARTOPIAN_LAUNCH_CWD='$($env:CARTOPIAN_LAUNCH_CWD)' is not a directory"
        exit 1
    }
    $LaunchCwd = (Resolve-Path $env:CARTOPIAN_LAUNCH_CWD).Path
    Set-Location $LaunchCwd
    Write-Host "cartopian-opencode: cwd=$LaunchCwd (CARTOPIAN_LAUNCH_CWD override)" -ForegroundColor DarkGray
} else {
    $PromptAbs    = (Resolve-Path $PromptPath).Path
    $PromptsDir   = Split-Path -Parent $PromptAbs
    $ProjectDir   = Split-Path -Parent $PromptsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts') {
        Set-Location $ProjectDir
        Write-Host "cartopian-opencode: cwd=$ProjectDir" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-opencode: prompt is outside a Cartopian project layout; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
    }
}
# --------------------------------------------------------------------

$Args = @('run')
# --auto on by default: opencode needs no path-widening flag; --auto only
# bypasses configured `ask` rules, which auto-reject in non-interactive runs.
if ($OpencodeAuto) {
    $Args += '--auto'
}
# Agent-neutral model selection: dispatch exports CARTOPIAN_MODEL from the
# resolved dispatch model; translate it into opencode's --model flag.
# Unset means opencode's own default model.
if ($env:CARTOPIAN_MODEL) {
    $Args += @('--model', $env:CARTOPIAN_MODEL)
}
# Agent-neutral effort selection: dispatch exports CARTOPIAN_EFFORT from the
# resolved dispatch effort; translate it into opencode's --variant flag
# (provider-specific reasoning effort). Values outside opencode's CLI-wide
# variant vocabulary fall back to the default effort (warn + omit). A
# vocabulary-valid variant a specific model does not offer is passed through —
# that outcome is the tool's own behavior. The vocabulary tracks the installed
# opencode CLI generation and may drift as it evolves.
if ($env:CARTOPIAN_EFFORT) {
    $EffortLc = $env:CARTOPIAN_EFFORT.ToLowerInvariant()
    if ($EffortLc -in @('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'thinking')) {
        $Args += @('--variant', $EffortLc)
    } else {
        [Console]::Error.WriteLine("cartopian-opencode: CARTOPIAN_EFFORT=$($env:CARTOPIAN_EFFORT) is not a supported opencode variant (none|minimal|low|medium|high|xhigh|max|thinking); launching with the default effort")
    }
}
# Optional opencode agent selection (opencode's own agent concept).
if ($env:CARTOPIAN_OPENCODE_AGENT) {
    $Args += @('--agent', $env:CARTOPIAN_OPENCODE_AGENT)
}
# Work-root no-op notice: opencode has no path-widening flag, so declared work
# roots need no grant here. Actual access remains subject to OS permissions
# and any operator-configured permission rules.
if ($env:CARTOPIAN_WORK_ROOTS) {
    [Console]::Error.WriteLine("cartopian-opencode: opencode has no path-widening flag; work roots need no grant here, and actual access remains subject to OS permissions and any operator permission rules (CARTOPIAN_WORK_ROOTS=$($env:CARTOPIAN_WORK_ROOTS))")
}
$Args += $PromptPathAbs

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

Write-Host "cartopian-opencode: running opencode run (auto=$OpencodeAuto, timeout=$TimeoutSpec)" -ForegroundColor DarkGray

# Run under the report-completion supervisor (parity with the bash
# cartopian_run_supervised): once the authoritative report file appears, a
# lingering child is reaped promptly so a finished handoff exits 0/clean
# instead of idling to the CARTOPIAN_TIMEOUT deadline. The deadline (the
# single SSOT timer, enforced inside the supervisor) is untouched -- a genuine
# hang that writes no report still hits it (exit 124). The watched report path
# is the status path without its ".status" suffix (shared derivation --
# Get-CartopianReportPath in CartopianStatus.ps1 owns the suffix contract).
$ReportPath = Get-CartopianReportPath $StatusPath

$run = Invoke-CartopianSupervisedRun -ReportPath $ReportPath -FilePath opencode -ArgumentList $Args -TimeoutSec $TimeoutSec
if ($run.TimedOut) {
    Write-Host "cartopian-opencode: timeout after $TimeoutSpec -- process killed (exit 124)" -ForegroundColor DarkYellow
}
Write-CartopianStatus -StatusPath $StatusPath -ExitCode $run.ExitCode -TimedOut $run.TimedOut
exit $run.ExitCode
