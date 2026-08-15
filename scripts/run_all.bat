@echo off
rem Wrapper for run_all.sh on Windows. Calls bash from Git for Windows.
rem Usage: run_all.bat [up|start|stop]

setlocal
chcp 65001 >nul

set "BASH="
for %%P in (
    "C:\Program Files\Git\bin\bash.exe"
    "C:\Program Files\Git\usr\bin\bash.exe"
    "%ProgramFiles%\Git\bin\bash.exe"
    "%ProgramFiles(x86)%\Git\bin\bash.exe"
    "%LOCALAPPDATA%\Programs\Git\bin\bash.exe"
) do (
    if exist "%%~P" set "BASH=%%~P"
)

if not defined BASH (
    for /f "usebackq delims=" %%B in (`where bash 2^>nul`) do set "BASH=%%B"
)

if not defined BASH (
    echo ERROR: bash.exe from Git for Windows not found.
    echo Install Git for Windows: https://git-scm.com/download/win
    exit /b 1
)

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."

"%BASH%" -lc "cd '%ROOT_DIR:\=/%' && ./scripts/run_all.sh %*"
exit /b %ERRORLEVEL%
