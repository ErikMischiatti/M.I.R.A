@echo off
setlocal
set "MIRA_ROOT=%~dp0.."
if exist "%MIRA_ROOT%\.venv\Scripts\python.exe" (
  "%MIRA_ROOT%\.venv\Scripts\python.exe" -m mira.main %*
) else if exist "%MIRA_ROOT%\.windows-runtime\python.exe" (
  "%MIRA_ROOT%\.windows-runtime\python.exe" -m mira.main %*
) else (
  echo mira: create a native Windows Python 3.12 environment. See docs\development.md.
  exit /b 1
)
