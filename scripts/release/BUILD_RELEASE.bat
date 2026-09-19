@echo off
setlocal EnableExtensions

if "%~1"=="" (
  echo Usage: BUILD_RELEASE.bat ^<version^>
  exit /b 2
)

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%..\.."

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3.12 "%SCRIPT_DIR%build_release.py" "%~1"
) else (
  python "%SCRIPT_DIR%build_release.py" "%~1"
)

exit /b %ERRORLEVEL%
