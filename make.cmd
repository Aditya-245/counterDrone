@echo off
REM SKYGYM - Windows fallback shim so the Makefile targets can be run on stock
REM Windows without GNU Make installed.   Usage:   .\make.cmd check
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_make.ps1" %*
exit /b %ERRORLEVEL%