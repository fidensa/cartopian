<#
.SYNOPSIS
    Cartopian wrapper for the Claude Code CLI (PowerShell).

.DESCRIPTION
    Reads a Cartopian prompt file and passes its content to claude -p
    with non-interactive flags.

.PARAMETER PromptPath
    Absolute path to the Cartopian prompt file.

.EXAMPLE
    .\cartopian-claude.ps1 C:\projects\cartopian\projects\myproject\prompts\PROMPT-NN-NNN.md
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$PromptPath
)

$ErrorActionPreference = 'Stop'

# --- Status-file helper (early-crash signal for wait-handoff) --------
# Dot-source the shared helper that emits <report-path>.status on assignee
# exit. A standalone, non-mediated wrapper invocation may retain the historical
# fallback below. Hook-bound dispatches require the installed helper chain and
# refuse before probing Claude if it is incomplete.
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
# AllowedTools restricts which tools claude can use. Empty (default)
# means claude uses its full default tool set, which is what an
# autonomous coder/reviewer handoff needs.
$AllowedTools = if ($env:CARTOPIAN_CLAUDE_TOOLS) { $env:CARTOPIAN_CLAUDE_TOOLS } else { '' }
$OutputFormat = if ($env:CARTOPIAN_CLAUDE_FORMAT) { $env:CARTOPIAN_CLAUDE_FORMAT } else { 'text' }
# Hook-enabled Cartopian launches reject bare mode because Claude suppresses
# even explicit settings-file/flag hooks under --bare.
$Bare = if ($env:CARTOPIAN_CLAUDE_BARE -eq 'true') { $true } else { $false }
# Skip permission prompts so claude runs non-interactively. Matches
# the autonomy posture of cartopian-codex and cartopian-agy. Set
# CARTOPIAN_CLAUDE_SKIP_PERMS=false to re-enable prompts.
$SkipPermissions = if ($env:CARTOPIAN_CLAUDE_SKIP_PERMS -eq 'false') { $false } else { $true }
# ------------------------------------------------------------------

if (-not (Test-Path $PromptPath)) {
    Write-Error "cartopian-claude: prompt file not found: $PromptPath"
    exit 1
}

$HookBound = [bool]($env:CARTOPIAN_ROLE -or $env:CARTOPIAN_EXPECTED_REPORT_PATH)
$StatusHelperPresent = Test-Path -LiteralPath $CartopianStatusModule -PathType Leaf
if ($HookBound -and -not $StatusHelperPresent) {
    Write-Error "cartopian-claude: hook-enabled launches require the installed status/supervisor helper: $CartopianStatusModule"
    exit 1
}
$ClaudeExecutable = $env:CARTOPIAN_CLAUDE_EXECUTABLE
if ($HookBound) {
    if (
        -not $env:CARTOPIAN_LAUNCH_CWD -or
        -not [IO.Path]::IsPathRooted($env:CARTOPIAN_LAUNCH_CWD) -or
        -not (Test-Path -LiteralPath $env:CARTOPIAN_LAUNCH_CWD -PathType Container)
    ) {
        Write-Error 'cartopian-claude: hook-enabled launches require an absolute, existing CARTOPIAN_LAUNCH_CWD from cartopian dispatch'
        exit 1
    }
    if (-not [IO.Path]::IsPathRooted($PromptPath)) {
        Write-Error 'cartopian-claude: hook-enabled launches require an absolute prompt path from cartopian dispatch'
        exit 1
    }
    if (
        -not $ClaudeExecutable -or
        -not [IO.Path]::IsPathRooted($ClaudeExecutable) -or
        -not (Test-Path -LiteralPath $ClaudeExecutable -PathType Leaf)
    ) {
        Write-Error 'cartopian-claude: hook-enabled launches require an absolute CARTOPIAN_CLAUDE_EXECUTABLE file from cartopian dispatch'
        exit 1
    }
    if ([IO.Path]::GetExtension($ClaudeExecutable).ToLowerInvariant() -in @('.cmd', '.bat')) {
        Write-Error 'cartopian-claude: hook-enabled native-Windows launches require a native Claude executable; .cmd/.bat shims cannot preserve the exact settings argv boundary'
        exit 1
    }
    $PythonPath = $env:CARTOPIAN_PYTHON
    if (
        -not $PythonPath -or
        -not [IO.Path]::IsPathRooted($PythonPath) -or
        -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)
    ) {
        Write-Error 'cartopian-claude: hook-enabled launches require an absolute CARTOPIAN_PYTHON file from cartopian dispatch'
        exit 1
    }
} elseif (-not $ClaudeExecutable) {
    $ClaudeCommand = Get-Command -Name claude -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($ClaudeCommand) { $ClaudeExecutable = [string]$ClaudeCommand.Source }
}
if (
    -not $ClaudeExecutable -or
    -not [IO.Path]::IsPathRooted($ClaudeExecutable) -or
    -not (Test-Path -LiteralPath $ClaudeExecutable -PathType Leaf)
) {
    Write-Error 'cartopian-claude: underlying Claude executable is missing, non-absolute, or not a file. Install Claude Code and launch through cartopian dispatch.'
    exit 1
}
$env:CARTOPIAN_CLAUDE_EXECUTABLE = $ClaudeExecutable

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
if ($HookBound) {
    $LaunchCwd = [IO.Path]::GetFullPath($env:CARTOPIAN_LAUNCH_CWD)
    Set-Location -LiteralPath $LaunchCwd
    Write-Host "cartopian-claude: cwd=$LaunchCwd (dispatch boundary)" -ForegroundColor DarkGray
} elseif ($env:CARTOPIAN_LAUNCH_CWD) {
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

$Args = @('-p')

# CARTOPIAN_ROLE is the mediated-dispatch role/config boundary consumed by the
# capability hook; CARTOPIAN_EXPECTED_REPORT_PATH independently activates the
# completion hook. Generate one process-scoped --settings value. Activated
# launches disable filesystem settings sources because user/plugin command
# hooks execute outside shell containment. A generation or compatibility
# failure refuses the launch.
if ($env:CARTOPIAN_ROLE -or $env:CARTOPIAN_EXPECTED_REPORT_PATH) {
    $InstallRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    $SettingsHelper = Join-Path $InstallRoot 'cli\claude_launch_settings.py'
    if (-not (Test-Path -LiteralPath $SettingsHelper -PathType Leaf)) {
        Write-Error "cartopian-claude: Claude settings helper not found: $SettingsHelper"
        exit 1
    }
    $ProjectDir = (Get-Location).Path
    $SettingsHelperArgs = @(
        $SettingsHelper,
        '--install-root', $InstallRoot,
        '--project-dir', $ProjectDir,
        '--platform', 'windows'
    )
    if ($env:CARTOPIAN_ROLE) { $SettingsHelperArgs += '--capability' }
    if ($env:CARTOPIAN_EXPECTED_REPORT_PATH) { $SettingsHelperArgs += '--completion' }
    & $PythonPath -I -S @SettingsHelperArgs --preflight-only | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'cartopian-claude: pre-containment Claude launch validation failed'
        exit 1
    }
    $ClaudeVersionOutput = (& $ClaudeExecutable --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $ClaudeVersionOutput) {
        Write-Error 'cartopian-claude: could not determine Claude Code version'
        exit 1
    }
    $SettingsHelperArgs += @('--claude-version', $ClaudeVersionOutput)
    $ClaudeLaunchSettings = & $PythonPath -I -S @SettingsHelperArgs
    if ($LASTEXITCODE -ne 0 -or -not $ClaudeLaunchSettings) {
        Write-Error 'cartopian-claude: could not construct process-scoped Claude hook settings'
        exit 1
    }
    $ClaudeLaunchSettingsJson = $ClaudeLaunchSettings -join "`n"
    if ($ClaudeLaunchSettingsJson -ne '{}') {
        if ($Bare) {
            Write-Error 'cartopian-claude: CARTOPIAN_CLAUDE_BARE=true suppresses required process-scoped hooks'
            exit 1
        }
        if ($ClaudeLaunchSettingsJson -match '"PreToolUse":') {
            # Refuse user/plugin MCP surfaces plus delegated/worktree
            # relocation outside the captured capability boundary.
            $Args += '--strict-mcp-config'
            $Args += @('--disallowedTools', 'Agent,Task,EnterWorktree,ExitWorktree,TeamCreate,TeamDelete,CronCreate,CronDelete,CronList,SendMessage,SendFile,RemoteTrigger')
            $Args += @('--setting-sources', '')
            $env:CLAUDE_CODE_DISABLE_AUTO_MEMORY = '1'
        }
        $Args += @('--settings', $ClaudeLaunchSettingsJson)
    }
}

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
# Agent-neutral model selection: dispatch exports CARTOPIAN_MODEL from the
# resolved dispatch model; translate it into claude's --model flag.
# Unset means claude's own default model.
if ($env:CARTOPIAN_MODEL) {
    $Args += @('--model', $env:CARTOPIAN_MODEL)
}
# Agent-neutral effort selection: dispatch exports CARTOPIAN_EFFORT from the
# resolved dispatch effort; translate it into claude's --effort flag.
# Values outside claude's CLI-wide effort vocabulary fall back to the default
# effort (warn + omit). A vocabulary-valid level a specific model rejects is
# passed through — that outcome is the tool's own behavior. The vocabulary
# tracks the installed claude CLI generation and may drift as it evolves.
if ($env:CARTOPIAN_EFFORT) {
    $EffortLc = $env:CARTOPIAN_EFFORT.ToLowerInvariant()
    if ($EffortLc -in @('low', 'medium', 'high', 'xhigh', 'max')) {
        $Args += @('--effort', $EffortLc)
    } else {
        [Console]::Error.WriteLine("cartopian-claude: CARTOPIAN_EFFORT=$($env:CARTOPIAN_EFFORT) is not a supported claude effort level (low|medium|high|xhigh|max); launching with the default effort")
    }
}
# Claude 2.1.212+ parses --add-dir as variadic. Keep the positional prompt
# before every --add-dir occurrence so the final variadic option cannot
# consume it as another directory.
$Args += $PromptPathAbs

# Work-root grant: dispatch exports CARTOPIAN_WORK_ROOTS (a pathsep-joined
# list — ';' on Windows — of the project's resolved work-root absolute
# paths). Declared work roots become additional working directories
# (--add-dir) so writes there are in-scope in every permission mode — an
# explicit grant, not a side effect of --dangerously-skip-permissions.
if ($env:CARTOPIAN_WORK_ROOTS) {
    foreach ($root in ($env:CARTOPIAN_WORK_ROOTS -split [IO.Path]::PathSeparator)) {
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

$TraceTools = if ($AllowedTools) { $AllowedTools } else { 'default' }
Write-Host "cartopian-claude: running claude -p (tools=$TraceTools, skip-perms=$SkipPermissions, timeout=$TimeoutSpec)" -ForegroundColor DarkGray

# Run under the report-completion supervisor (parity with the bash
# cartopian_run_supervised): once the authoritative report file appears, a
# lingering child is reaped promptly so a finished handoff exits 0/clean
# instead of idling to the CARTOPIAN_TIMEOUT deadline. The deadline (the
# single SSOT timer, enforced inside the supervisor) is untouched -- a genuine
# hang that writes no report still hits it (exit 124). The watched report path
# is the status path without its ".status" suffix (shared derivation --
# Get-CartopianReportPath in CartopianStatus.ps1 owns the suffix contract).
$ReportPath = Get-CartopianReportPath $StatusPath

$run = Invoke-CartopianSupervisedRun -ReportPath $ReportPath -FilePath $ClaudeExecutable -ArgumentList $Args -TimeoutSec $TimeoutSec
if ($run.TimedOut) {
    Write-Host "cartopian-claude: timeout after $TimeoutSpec -- process killed (exit 124)" -ForegroundColor DarkYellow
}
Write-CartopianStatus -StatusPath $StatusPath -ExitCode $run.ExitCode -TimedOut $run.TimedOut
exit $run.ExitCode
