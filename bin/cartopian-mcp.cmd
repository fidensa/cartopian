@echo off
rem Native-Windows shim for bin/cartopian-mcp.
rem Forwards every argument to the extensionless Python entrypoint that lives
rem in the same directory. Exit code propagates from the interpreter.
rem
rem Many Windows hosts still have a legacy Python 2 installation that owns the
rem bare ``python`` name on PATH, so the shim probes the Python Launcher
rem (``py -3``) first and only falls back to ``python`` when the launcher is
rem absent. ``bin/cartopian-mcp`` enforces the 3.11+ baseline itself.
py -3 -V >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0cartopian-mcp" %*
    exit /b %ERRORLEVEL%
)
python "%~dp0cartopian-mcp" %*
exit /b %ERRORLEVEL%
