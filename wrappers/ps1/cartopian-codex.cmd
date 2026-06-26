@echo off
rem Native-Windows PATH shim for the cartopian-codex assignee wrapper.
rem
rem The wrapper itself is the sibling PowerShell script. A bare `.ps1` does not
rem resolve as a command from PATH (`.PS1` is not in PATHEXT), and `cartopian
rem dispatch` launches the agent via CreateProcess, which cannot execute a
rem `.ps1` directly. This `.cmd` (in PATHEXT) is what makes `cartopian-codex`
rem resolve as a bare command and lets dispatch launch it.
rem
rem Prefer PowerShell 7 (`pwsh`); fall back to Windows PowerShell 5.1
rem (`powershell`). Forwards every argument and propagates the exit code.
where /q pwsh
if not errorlevel 1 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0cartopian-codex.ps1" %*
    exit /b %ERRORLEVEL%
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cartopian-codex.ps1" %*
exit /b %ERRORLEVEL%
