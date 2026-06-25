<#
.SYNOPSIS
    Cartopian wrapper for the Devin CLI (PowerShell).

.DESCRIPTION
    Passes a Cartopian prompt file path to devin -p --prompt-file
    with non-interactive flags.

.PARAMETER PromptPath
    Absolute path to the Cartopian prompt file.

.EXAMPLE
    .\cartopian-devin.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-NN-NNN.md
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
    function Get-CartopianScopeArgs { return @() }
    function Get-CartopianCommentDirective { param([string]$Level) return '' }
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
# CARTOPIAN_DEVIN_PERMISSION selects an ABSTRACT permission posture, mapped
# onto whichever surface the INSTALLED devin binary exposes (probed via
# parser acceptance after the CLI check below; see tests/wrappers/pm-devin/
# FINDINGS.md and the bash wrapper for the full rationale):
#  * FOUR-MODE surface (cli.devin.ai docs, captured 2026-06-02):
#    `--permission-mode normal|accept-edits|bypass|autonomous` + `--sandbox`.
#  * TWO-MODE surface (live `devin 2026.5.26-3`, probed 2026-06-04): only
#    `normal` (alias auto) and `dangerous` (aliases yolo, bypass) are valid;
#    `autonomous`/`accept-edits` are REJECTED at argv parse. `--sandbox`
#    exists independently.
# Abstract mode -> composition:
#   normal        both surfaces: --permission-mode normal
#   accept-edits  four-mode: --permission-mode accept-edits
#                 two-mode:  NO equivalent — FAIL CLOSED before launch
#   bypass        both surfaces: --permission-mode bypass (two-mode alias of
#                 `dangerous`; auto-approve all, NO OS sandbox)
#   autonomous    four-mode: --sandbox --permission-mode autonomous
#                 two-mode:  --sandbox --permission-mode dangerous
#                 (same posture: auto-approve-all bounded by the OS sandbox)
# DEFAULT = 'autonomous': most-restrictive sensible mode that still completes
# the handoff with no human in the loop — the analogue of cartopian-codex's
# `workspace-write` sandbox default (OS-bounded autonomy, not full bypass).
# devin stays tier-3 not-recommended; the local --sandbox does not extend to
# devin's cloud /handoff. Legacy values are mapped onto the abstract modes:
#   auto -> normal ;  dangerous -> bypass
$PermissionMode = if ($env:CARTOPIAN_DEVIN_PERMISSION) { $env:CARTOPIAN_DEVIN_PERMISSION } else { 'autonomous' }
switch ($PermissionMode) {
    'auto'      { $PermissionMode = 'normal' }
    'dangerous' { $PermissionMode = 'bypass' }
}
if ($PermissionMode -notin @('normal', 'accept-edits', 'bypass', 'autonomous')) {
    Write-Error "cartopian-devin: unknown CARTOPIAN_DEVIN_PERMISSION='$PermissionMode' (valid: normal | accept-edits | bypass | autonomous; legacy auto->normal, dangerous->bypass)"
    exit 1
}
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-devin: prompt file not found: $PromptPath"
    exit 1
}

if (-not (Get-Command devin -ErrorAction SilentlyContinue)) {
    Write-Error "cartopian-devin: 'devin' not found in PATH. Install: https://docs.devin.ai/"
    exit 1
}

# --- Permission-surface detection ------------------------------------
# Probe the installed binary's ARGV PARSER, not its help prose: a parser that
# ACCEPTS `--permission-mode autonomous` (exit 0 with --help appended) is the
# four-mode surface; one that rejects it at parse (exit 2 — the live
# `devin 2026.5.26-3` behavior) is the two-mode surface, immune to help-text
# wording drift. Any probe failure — non-zero exit, a spawn that throws, or
# a probe that exceeds the 10s bound (killed; a hanging probe must not stall
# the wrapper outside the supervisor's SSOT deadline) — degrades to two-mode,
# the live-verified surface, never to a guessed flag value the binary would
# reject at launch. Mirrors wrappers/bin/cartopian-devin.
$DevinSurface = 'two-mode'
$ProbeOut = $null
$ProbeErr = $null
try {
    $ProbeOut = [System.IO.Path]::GetTempFileName()
    $ProbeErr = [System.IO.Path]::GetTempFileName()
    $probe = Start-Process -FilePath devin `
        -ArgumentList @('--permission-mode', 'autonomous', '--help') `
        -NoNewWindow -PassThru -ErrorAction Stop `
        -RedirectStandardOutput $ProbeOut -RedirectStandardError $ProbeErr
    if ($probe.WaitForExit(10000)) {
        if ($probe.ExitCode -eq 0) { $DevinSurface = 'four-mode' }
    } else {
        try { $probe.Kill() } catch {}
    }
} catch {
    $DevinSurface = 'two-mode'
} finally {
    foreach ($f in @($ProbeOut, $ProbeErr)) {
        if ($f) { try { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue } catch {} }
    }
}

$PromptPathAbs = (Resolve-Path $PromptPath).Path

# Derive the optional status-file path now, before any Set-Location, so a
# relative prompt path still resolves. $null when outside a project layout.
$StatusPath = Get-CartopianStatusPath $PromptPath

# --- Launch directory ------------------------------------------------
# Assignee CLIs run with cwd set to the Cartopian project root
# (the registered project path). Prompts always live at
# <workspace>/projects/<project-id>/prompts/PROMPT-*.md, so the project
# root is derivable from the prompt path alone.
if ($env:CARTOPIAN_LAUNCH_CWD) {
    if (-not (Test-Path -PathType Container $env:CARTOPIAN_LAUNCH_CWD)) {
        Write-Error "cartopian-devin: CARTOPIAN_LAUNCH_CWD='$($env:CARTOPIAN_LAUNCH_CWD)' is not a directory"
        exit 1
    }
    $LaunchCwd = (Resolve-Path $env:CARTOPIAN_LAUNCH_CWD).Path
    Set-Location $LaunchCwd
    Write-Host "cartopian-devin: cwd=$LaunchCwd (CARTOPIAN_LAUNCH_CWD override)" -ForegroundColor DarkGray
} else {
    $PromptsDir   = Split-Path -Parent $PromptPathAbs
    $ProjectDir   = Split-Path -Parent $PromptsDir
    if ((Split-Path -Leaf $PromptsDir) -eq 'prompts') {
        Set-Location $ProjectDir
        Write-Host "cartopian-devin: cwd=$ProjectDir" -ForegroundColor DarkGray
    } else {
        Write-Host "cartopian-devin: prompt is outside a Cartopian project layout; leaving cwd unchanged (set CARTOPIAN_LAUNCH_CWD to override)" -ForegroundColor DarkGray
    }
}
# --------------------------------------------------------------------

# --- Access grants ---------------------------------------------------
# devin exposes no native multi-directory write-scoping flag, so the shared
# helper fails closed on a non-empty work-root scope (the [work-root] stderr
# line) unless CARTOPIAN_DEVIN_UNRESTRICTED=true. It honors the mediated
# launcher's explicit CARTOPIAN_SCOPE_DIRS even when cartopian is absent from
# PATH, so a scoped dispatch never silently launches unscoped. Passing an empty
# scope flag selects the no-native-scoping (fail-closed) path; devin injects no
# scope args into its own command.
$null = Get-CartopianScopeArgs -Wrapper 'cartopian-devin' -ScopeFlag '' -CommaJoin $false -Unrestricted ($env:CARTOPIAN_DEVIN_UNRESTRICTED -eq 'true') -VarName 'CARTOPIAN_DEVIN_UNRESTRICTED'
# --------------------------------------------------------------------

# Map the abstract mode onto the DETECTED surface (see Configuration). On the
# four-mode surface `autonomous` requires the OS sandbox; on the two-mode
# surface the same posture is spelled `--sandbox --permission-mode dangerous`.
# `accept-edits` has no two-mode equivalent and FAILS CLOSED rather than
# passing a value devin rejects. `normal`/`bypass` are valid on both surfaces.
if ($PermissionMode -eq 'autonomous') {
    if ($DevinSurface -eq 'four-mode') {
        $Args = @('-p', '--sandbox', '--permission-mode', 'autonomous')
    } else {
        $Args = @('-p', '--sandbox', '--permission-mode', 'dangerous')
    }
} elseif ($PermissionMode -eq 'accept-edits') {
    if ($DevinSurface -eq 'four-mode') {
        $Args = @('-p', '--permission-mode', 'accept-edits')
    } else {
        [Console]::Error.WriteLine("cartopian-devin: error: the installed devin CLI exposes no 'accept-edits' permission mode (two-mode surface: normal|dangerous)")
        [Console]::Error.WriteLine("  choose CARTOPIAN_DEVIN_PERMISSION=normal | bypass | autonomous, or update the devin CLI")
        exit 1
    }
} else {
    $Args = @('-p', '--permission-mode', $PermissionMode)
}
# Agent-neutral model selection: dispatch exports CARTOPIAN_MODEL from the
# resolved [handoffs.<role>].model; translate it into devin's --model flag.
# Unset means devin's own default model.
if ($env:CARTOPIAN_MODEL) {
    $Args += @('--model', $env:CARTOPIAN_MODEL)
}
# devin takes the prompt by file, not content, so the comment-volume directive
# plus the always-on management-identifier ban are prepended into a temp prompt
# file (the operator's original is never mutated). Status/report-path derivation
# still uses the original prompt path, so this only changes what devin reads.
$EffectivePromptPath = $PromptPathAbs
if ($env:CARTOPIAN_CODE_COMMENTS) {
    $tmpPrompt = [System.IO.Path]::GetTempFileName()
    $body = (Get-CartopianCommentDirective $env:CARTOPIAN_CODE_COMMENTS) + "`n`n" + (Get-Content -Path $PromptPathAbs -Raw)
    Set-Content -LiteralPath $tmpPrompt -Value $body -NoNewline -Encoding utf8
    $EffectivePromptPath = $tmpPrompt
}
$Args += @('--prompt-file', $EffectivePromptPath)

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

Write-Host "cartopian-devin: running devin -p (permission=$PermissionMode, surface=$DevinSurface, timeout=$TimeoutSpec)" -ForegroundColor DarkGray

# Run under the report-completion supervisor (parity with the bash
# cartopian_run_supervised): once the authoritative report file appears, a
# lingering child is reaped promptly so a finished handoff exits 0/clean
# instead of idling to the CARTOPIAN_TIMEOUT deadline. The deadline (the
# single SSOT timer, enforced inside the supervisor) is untouched — a genuine
# hang that writes no report still hits it (exit 124). The watched report path
# is the status path without its ".status" suffix (shared derivation —
# Get-CartopianReportPath in CartopianStatus.ps1 owns the suffix contract).
$ReportPath = Get-CartopianReportPath $StatusPath

$run = Invoke-CartopianSupervisedRun -ReportPath $ReportPath -FilePath devin -ArgumentList $Args -TimeoutSec $TimeoutSec
if ($run.TimedOut) {
    Write-Host "cartopian-devin: timeout after $TimeoutSpec — process killed (exit 124)" -ForegroundColor DarkYellow
}
Write-CartopianStatus -StatusPath $StatusPath -ExitCode $run.ExitCode -TimedOut $run.TimedOut
# Remove the directive-enriched temp prompt (parity with the bash wrapper's trap).
if ($EffectivePromptPath -ne $PromptPathAbs) {
    Remove-Item -LiteralPath $EffectivePromptPath -Force -ErrorAction SilentlyContinue
}
exit $run.ExitCode
