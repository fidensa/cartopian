@echo off
rem Native-Windows shim for bin/cartopian-mcp.
rem Forwards every argument to the extensionless Python entrypoint that lives
rem in the same directory. Exit code propagates from python automatically.
python "%~dp0cartopian-mcp" %*
