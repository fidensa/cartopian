<#
.SYNOPSIS
    Shared status-file helper for the Cartopian PowerShell agent wrappers.

.DESCRIPTION
    Dot-sourced by every cartopian-*.ps1 wrapper. Emits the optional
    early-crash detection signal that `cartopian wait-handoff` consumes: a
    status file at the deterministic path `<report-path>.status` capturing
    the assignee process exit outcome.

    CONSUMER CONTRACT (cli/commands/wait_handoff.py :: _status_exit_code):
      - Path:   <project-root>\reports\REPORT-<NN-NNN>.md.status
                (the expected report path with a ".status" suffix).
      - Format: newline-separated `key=value` lines. The consumer parses
                `state` and `exit_code`; it treats `state=exited` with a
                NON-ZERO `exit_code` as the crash signal (status `failed`)
                and ignores every other key. A zero exit_code is NOT a crash.
      - Absence is valid: wait-handoff falls back to the report-only path, so
        this helper NEVER fails the wrapper — all errors degrade to a no-op.

    Fields written (mirrors wrappers/bin/_cartopian-status.sh exactly):
      state=exited           always, once the assignee process has exited
      exit_code=<int>        the assignee exit code (124 for a timeout kill)
      reason=clean|error|timeout

    No secrets or environment data are ever written — only the three fields
    above, all derived from the exit outcome.
#>

# Derive the status-file path wait-handoff expects from the prompt path.
# Returns the absolute "<report-path>.status" path, or $null when it cannot
# be derived (prompt outside a Cartopian project layout, or no NN-NNN id).
function Get-CartopianStatusPath {
    param([string]$PromptPath)
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
    if ($base -match '(\d{2}-\d{3})') {
        $id = $Matches[1]
    } else {
        return $null
    }
    return (Join-Path $projectDir (Join-Path 'reports' "REPORT-$id.md.status"))
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
        # Newline-delimited key=value; "`n" only — the consumer's splitlines()
        # accepts LF and the format must match the Bash producer byte-for-byte.
        $content = "state=exited`nexit_code=$code`nreason=$reason`n"
        $tmp = "$StatusPath.tmp"
        Set-Content -LiteralPath $tmp -Value $content -NoNewline -Encoding utf8
        Move-Item -LiteralPath $tmp -Destination $StatusPath -Force
    } catch {
        # Fail-open: status file is an optional optimization, never required.
    }
}
