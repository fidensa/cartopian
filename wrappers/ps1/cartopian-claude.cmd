@echo off
setlocal DisableDelayedExpansion
rem Dispatch carries the batch target and prompt through environment variables
rem so cmd.exe never reparses operator-controlled paths as command text. They
rem have already expanded into this shim's argv; do not leak the transport
rem variables into Claude or its hooks.
set "CARTOPIAN_WINDOWS_AGENT_EXECUTABLE="
set "CARTOPIAN_WINDOWS_PROMPT_PATH="
rem Native-Windows PATH shim for the cartopian-claude assignee wrapper.
rem
rem The wrapper itself is the sibling PowerShell script. A bare `.ps1` does not
rem resolve as a command from PATH (`.PS1` is not in PATHEXT), and `cartopian
rem dispatch` launches the agent via CreateProcess, which cannot execute a
rem `.ps1` directly. This `.cmd` (in PATHEXT) is what makes `cartopian-claude`
rem resolve as a bare command and lets dispatch launch it.
rem
rem Use only fixed system installation paths. A PATH lookup here would execute
rem before Cartopian can validate the activated launch boundary.
set "CARTOPIAN_POWERSHELL="
if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "CARTOPIAN_POWERSHELL=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined CARTOPIAN_POWERSHELL if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "CARTOPIAN_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not defined CARTOPIAN_POWERSHELL (
    echo cartopian-claude: error: PowerShell was not found at a trusted system path 1>&2
    exit /b 1
)
"%CARTOPIAN_POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0cartopian-claude.ps1" %*
exit /b %ERRORLEVEL%
