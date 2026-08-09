<#
.SYNOPSIS
    Shared status-file helper for the Cartopian PowerShell agent wrappers.

.DESCRIPTION
    Dot-sourced by every cartopian-*.ps1 wrapper. Emits the optional
    early-crash detection signal that `cartopian wait-handoff` consumes: a
    status file at the deterministic path `<report-path>.status` capturing
    the assignee process exit outcome.

    CONSUMER CONTRACT (cli/commands/wait_handoff.py :: _status_exit_code):
      - Path:   the expected report path with a ".status" suffix --
                <project-root>\reports\REPORT-<NN-NNN>.md.status for task
                assignment, REPORT-<NN-NNN>-review.md.status for task review,
                REPORT-PLAN-<...>.md.status for planning review.
      - Format: newline-separated key=value lines carrying state, exit facts,
                launch_id, and expected_variant. Dispatch may publish running;
                this helper atomically replaces it on exit.
      - Absence is valid: wait-handoff falls back to the report-only path, so
        this helper NEVER fails the wrapper -- all errors degrade to a no-op.

    Fields written (mirrors wrappers/bin/_cartopian-status.sh exactly):
      state=exited           always, once the assignee process has exited
      exit_code=<int>        the assignee exit code (124 for a timeout kill)
      reason=clean|error|timeout

    No secrets or prompt content are written. Optional identity/variant fields
    come only from bounded dispatch exports.
#>

# Derive the status-file path wait-handoff expects from the prompt path.
# Returns the absolute "<report-path>.status" path, or $null when it cannot
# be derived (prompt outside a Cartopian project layout, or no NN-NNN id).
function Get-CartopianStatusPath {
    param([string]$PromptPath)
    # Bounded dispatch export wins: it names the exact report slot recorded
    # for this launch. A task-review handoff publishes to the independent
    # REPORT-NN-NNN-review.md slot while the coder's completion report is
    # preserved at REPORT-NN-NNN.md.
    $exported = $env:CARTOPIAN_EXPECTED_REPORT_PATH
    if ($exported -and [System.IO.Path]::IsPathRooted($exported)) {
        return "$exported.status"
    }
    if (-not $PromptPath) { return $null }
    try {
        $promptAbs = (Resolve-Path -LiteralPath $PromptPath -ErrorAction Stop).Path
    } catch {
        return $null
    }
    $promptsDir = Split-Path -Parent $promptAbs
    if ((Split-Path -Leaf $promptsDir) -ne 'prompts') { return $null }
    $projectDir = Split-Path -Parent $promptsDir
    $base = Split-Path -Leaf $promptAbs
    if ($base -match '^PROMPT-(PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?|\d{2}-\d{3})\.md$') {
        $id = $Matches[1]
    } else {
        return $null
    }
    # Manual review launches without the dispatch export: the task prompt
    # slot is shared between assignment and review, so the exported expected
    # report variant selects the independent review-report identity.
    $suffix = ''
    if ($env:CARTOPIAN_EXPECTED_REPORT_VARIANT -eq 'review' -and $id -match '^\d{2}-\d{3}$') {
        $suffix = '-review'
    }
    return (Join-Path $projectDir (Join-Path 'reports' "REPORT-$id$suffix.md.status"))
}

# Return $true when the file at -ReportPath looks like a *complete* handoff
# report: present, non-empty, and carrying a top-level
# `Status: <complete|blocked|failed>` line -- the authoritative completion
# signal `wait-handoff` keys off (it parses the same report). This is the
# producer-side proxy the wrappers use to decide a handoff is done; it
# deliberately accepts blocked/failed reports (a written report is a finished
# handoff regardless of verdict -- the PM reads the verdict). The `<` of the
# template placeholder (`Status: <complete | ...>`) does NOT match, so an
# unfilled template is not mistaken for a finished report. Pure, no side
# effects; any error degrades to "not complete" ($false). Mirrors
# bin/_cartopian-status.sh :: cartopian_report_complete exactly.
function Test-CartopianReportComplete {
    param([string]$ReportPath)
    if (-not $ReportPath) { return $false }
    try {
        if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) { return $false }
        $lines = [System.IO.File]::ReadAllLines($ReportPath)
    } catch {
        return $false
    }
    $hasStatus = $false
    foreach ($line in $lines) {
        if ($line -match '^(?i)Status:\s*(complete|blocked|failed)(\s|$)') {
            $hasStatus = $true
            break
        }
    }
    if (-not $hasStatus) { return $false }

    $text = $lines -join "`n"
    $variant = $env:CARTOPIAN_EXPECTED_REPORT_VARIANT
    if (-not $variant) {
        if ($text -match '(?m)^- Review ID:\s*REVIEW-PLAN-') {
            $variant = 'planning-review'
        } elseif ($text -match '(?m)^- Review ID:\s*REVIEW-') {
            $variant = 'review'
        } else {
            $variant = 'task'
        }
    }
    if ($variant -eq 'task') {
        return (
            $text -match '(?m)^##\s+Identity\s*$' -and
            $text -match '(?m)^##\s+Remaining risks\s*$' -and
            $text -match '(?m)^##\s+(Completion evidence|Files changed|Deliverable)\s*$' -and
            $text -match '(?ms)^##\s+(Ready to close|Ready for review)\s*$.*?^\s*(yes|no)\s*$'
        )
    }
    if ($variant -in @('review', 'planning-review')) {
        return (
            $text -match '(?m)^##\s+Identity\s*$' -and
            $text -match '(?m)^- Review ID:\s*REVIEW-' -and
            $text -match '(?m)^- Prompt path:\s*\S' -and
            $text -match '(?m)^- Review file path:\s*\S' -and
            ($variant -ne 'review' -or $text -match '(?m)^- Task path:\s*\S') -and
            $text -match '(?m)^Request alignment:\s*(aligned|drifted|unavailable-for-legacy)\s*$' -and
            $text -match '(?m)^Request evidence:\s*\S' -and
            $text -match '(?m)^##\s+Evidence reviewed\s*$' -and
            $text -match '(?ms)^##\s+Verdict\s*$.*?^\s*(approve|request-changes|reject)\s*$' -and
            $text -match '(?ms)^##\s+Blocking findings\s*$.*?^\s*\S'
        )
    }
    return $false
}

# Run the assignee command under the SSOT deadline, supervising for the
# authoritative report-completion signal so a *finished* assignee exits
# promptly instead of lingering until the deadline. Mirrors
# bin/_cartopian-status.sh :: cartopian_run_supervised (PowerShell
# parity).
#
# ROOT CAUSE this addresses: assignee CLIs can keep running after they have
# written the report -- MCP stdio servers not torn down, an inherited open
# stdin, or a trailing turn leave the process alive with no more work to do.
# The wrapper used to only WaitForExit on that process, so a finished handoff
# sat idle until the deadline killed it (exit 124, reason=timeout) -- a success
# that always read as a deadline failure.
#
# The fix is event-driven, NOT a second timer. On the bash side coreutils
# `timeout` enforces the single CARTOPIAN_TIMEOUT deadline; in PowerShell this
# function IS that single enforcer -- the deadline is computed ONCE from
# -TimeoutSec and never extended, and the report watch merely wakes the same
# wait loop in poll-sized slices (it imposes no clock of its own). Once the
# report is complete the child gets a brief grace to exit on its own and is
# then reaped, so the wrapper exits 0/clean promptly. A genuine hang never
# writes a report, is never reaped early, and still hits the deadline (124).
#
# stdin is redirected and closed immediately (the PowerShell equivalent of
# </dev/null) so the child can never block waiting on inherited terminal input
# (one of the lingering modes). ProcessStartInfo.ArgumentList is used where
# available so every argument remains exact; Windows PowerShell 5.1 falls back
# to an explicit CommandLineToArgvW-compatible quoting routine.
#
# Args:    -ReportPath   the authoritative report path to watch ($null/empty
#                        => no supervision; run under the deadline only)
#          -FilePath     the assignee executable
#          -ArgumentList its argv
#          -TimeoutSec   the CARTOPIAN_TIMEOUT deadline in seconds (SSOT)
# Returns: @{ ExitCode = <int>; TimedOut = <bool> }
#          ExitCode 0 whenever the report is complete (report authoritative --
#          a reaped lingering child is a SUCCESS, not a kill); 124 on a
#          genuine deadline kill; else the assignee's own exit code.
# Tunables (env): CARTOPIAN_REPORT_POLL (seconds between polls; default 2)
#                 CARTOPIAN_REPORT_GRACE_POLLS (post-report grace polls; default 3)
function ConvertTo-CartopianWindowsArgument {
    param([AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }

    $quoted = New-Object System.Text.StringBuilder
    [void]$quoted.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes++
            continue
        }
        if ($character -eq '"') {
            [void]$quoted.Append(('\' * (2 * $slashes + 1)))
            [void]$quoted.Append('"')
        } else {
            if ($slashes) { [void]$quoted.Append(('\' * $slashes)) }
            [void]$quoted.Append($character)
        }
        $slashes = 0
    }
    if ($slashes) { [void]$quoted.Append(('\' * (2 * $slashes))) }
    [void]$quoted.Append('"')
    return $quoted.ToString()
}

function Invoke-CartopianSupervisedRun {
    param(
        [AllowEmptyString()][AllowNull()][string]$ReportPath,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][object[]]$ArgumentList,
        [Parameter(Mandatory = $true)][int]$TimeoutSec
    )

    $pollSec = 2.0
    if ($env:CARTOPIAN_REPORT_POLL) {
        try { $pollSec = [double]$env:CARTOPIAN_REPORT_POLL } catch { $pollSec = 2.0 }
    }
    if ($pollSec -le 0) { $pollSec = 2.0 }
    $gracePolls = 3
    if ($env:CARTOPIAN_REPORT_GRACE_POLLS) {
        try { $gracePolls = [int]$env:CARTOPIAN_REPORT_GRACE_POLLS } catch { $gracePolls = 3 }
    }
    $pollMs = [int][Math]::Max(50.0, [double]$pollSec * 1000.0)

    # Start-Process joins -ArgumentList values into one string and can corrupt
    # JSON, quotes, and paths containing spaces on native Windows. Build the
    # process directly so modern PowerShell/.NET receives an exact argv array.
    # Windows PowerShell 5.1 lacks ProcessStartInfo.ArgumentList, so only that
    # runtime uses the compatible pre-quoted command-line fallback.
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    if ($startInfo.PSObject.Properties.Name -contains 'ArgumentList') {
        foreach ($argument in $ArgumentList) {
            [void]$startInfo.ArgumentList.Add([string]$argument)
        }
    } else {
        $startInfo.Arguments = (($ArgumentList | ForEach-Object {
            ConvertTo-CartopianWindowsArgument ([string]$_)
        }) -join ' ')
    }

    # The single SSOT deadline -- computed once, never extended.
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
    $timedOut = $false
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $startInfo
    [void]$proc.Start()
    $proc.StandardInput.Close()
    $reportSeen = $false
    while (-not $proc.HasExited) {
        $remainingMs = ($deadline - [DateTime]::UtcNow).TotalMilliseconds
        if ($remainingMs -le 0) {
            # Deadline elapsed: kill (the PowerShell analogue of coreutils
            # `timeout` sending SIGTERM and returning 124).
            try { $proc.Kill() } catch {}
            try { $proc.WaitForExit() } catch {}
            $timedOut = $true
            break
        }
        if (-not $reportSeen -and (Test-CartopianReportComplete $ReportPath)) {
            $reportSeen = $true
            # Work is provably done: grant a brief grace for the child to
            # tear itself down, then reap the lingerer.
            for ($i = 0; $i -lt $gracePolls -and -not $proc.HasExited; $i++) {
                $left = ($deadline - [DateTime]::UtcNow).TotalMilliseconds
                $g = [int][Math]::Min([double]$pollMs, [Math]::Max(1.0, $left))
                [void]$proc.WaitForExit($g)
            }
            if (-not $proc.HasExited) {
                try { $proc.Kill() } catch {}
                try { $proc.WaitForExit() } catch {}
            }
            break
        }
        $left = ($deadline - [DateTime]::UtcNow).TotalMilliseconds
        $w = [int][Math]::Min([double]$pollMs, [Math]::Max(1.0, $left))
        [void]$proc.WaitForExit($w)
    }

    # The report file is authoritative: if it is complete, the handoff
    # succeeded regardless of how the lingering child was ultimately reaped
    # (and even if the reap raced the deadline) -- mirroring the bash helper.
    if (Test-CartopianReportComplete $ReportPath) {
        return @{ ExitCode = 0; TimedOut = $false }
    }
    if ($timedOut) {
        return @{ ExitCode = 124; TimedOut = $true }
    }
    $code = 1
    try { $code = $proc.ExitCode } catch { $code = 1 }
    return @{ ExitCode = $code; TimedOut = $false }
}

# Derive the authoritative report path the supervisor watches from the status
# path: the same `<report>.status` mapping wait-handoff owns, minus the
# suffix. Single home for the suffix contract so the five wrappers cannot
# drift. $null when there is no status path (prompt outside a project layout).
function Get-CartopianReportPath {
    param([AllowEmptyString()][AllowNull()][string]$StatusPath)
    if (-not $StatusPath) { return $null }
    return $StatusPath -replace '\.status$', ''
}

# Write the status file capturing the assignee exit outcome. Best-effort and
# fail-open: any error degrades to a no-op so the wrapper's exit code is never
# disturbed.
#   -StatusPath : from Get-CartopianStatusPath ($null => skip)
#   -ExitCode   : assignee exit code (pass 124 for a timeout kill)
#   -TimedOut   : $true when the wrapper killed the child at the deadline
function Write-CartopianStatus {
    param(
        [string]$StatusPath,
        [int]$ExitCode,
        [bool]$TimedOut
    )
    if (-not $StatusPath) { return }
    try {
        if ($TimedOut) {
            # Deadline kill: keep the consumer-visible exit_code non-zero
            # (124, matching coreutils `timeout`) while flagging the reason.
            $code = 124
            $reason = 'timeout'
        } elseif ($ExitCode -eq 0) {
            $code = 0
            $reason = 'clean'
        } else {
            $code = $ExitCode
            $reason = 'error'
        }
        $dir = Split-Path -Parent $StatusPath
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
        }
        # Newline-delimited key=value; "`n" only -- the consumer's splitlines()
        # accepts LF and the format must match the Bash producer byte-for-byte.
        $content = "state=exited`nexit_code=$code`nreason=$reason`n"
        if ($env:CARTOPIAN_HANDOFF_ID) {
            $content += "launch_id=$($env:CARTOPIAN_HANDOFF_ID)`n"
        }
        if ($env:CARTOPIAN_EXPECTED_REPORT_VARIANT) {
            $content += "expected_variant=$($env:CARTOPIAN_EXPECTED_REPORT_VARIANT)`n"
        }
        $expectedLaunchLog = "$($StatusPath.Substring(0, $StatusPath.Length - '.status'.Length)).launch.log"
        if ($env:CARTOPIAN_LAUNCH_LOG_PATH -and
                $env:CARTOPIAN_LAUNCH_LOG_PATH -eq $expectedLaunchLog) {
            $content += "guarantee_scope=retained-launch-log`n"
            $content += "retained_log_ready=false`n"
        }
        $tmp = "$StatusPath.tmp"
        Set-Content -LiteralPath $tmp -Value $content -NoNewline -Encoding utf8
        Move-Item -LiteralPath $tmp -Destination $StatusPath -Force
    } catch {
        # Fail-open: status file is an optional optimization, never required.
    }
}
