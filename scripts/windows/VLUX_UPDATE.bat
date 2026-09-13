@echo off
setlocal EnableExtensions

if "%~1"=="" (
  echo Usage: VLUX_UPDATE.bat C:\Updates\VLUX_POS_x.y.z.zip
  exit /b 90
)
set "VLUX_PACKAGE=%~f1"
set "SCRIPT_DIR=%~dp0"

call "%SCRIPT_DIR%00_preflight.bat" || goto :fail
call "%SCRIPT_DIR%01_backup.bat" || goto :fail
call "%SCRIPT_DIR%02_stage_release.bat" "%VLUX_PACKAGE%" || goto :fail
call "%SCRIPT_DIR%03_verify_release.bat" "%VLUX_PACKAGE%" || goto :fail

call "%SCRIPT_DIR%vlux_env.bat" || goto :fail
for /f "usebackq delims=" %%S in ("%ODOO_HOME%\last_vlux_staged.txt") do set "STAGED=%%S"
for %%V in ("%STAGED%") do set "VLUX_RELEASE=%%~nxV"

if not "%ODOO_SERVICE_NAME%"=="" (
  sc stop "%ODOO_SERVICE_NAME%"
) else if exist "%ODOO_LOCAL_PID_FILE%" (
  for /f "usebackq delims=" %%P in ("%ODOO_LOCAL_PID_FILE%") do powershell -NoProfile -Command "Stop-Process -Id %%P -ErrorAction SilentlyContinue"
)

echo [activate] Activando release %VLUX_RELEASE%
robocopy "%STAGED%\addons" "%ACTIVE_ADDONS_ROOT%" /E /R:2 /W:2 /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 goto :fail_activate
echo %VLUX_RELEASE%>"%ODOO_HOME%\vlux_current_release.txt"

if exist "%STAGED%\requirements-extra.txt" (
  "%ODOO_VENV%\Scripts\python.exe" -m pip install -r "%STAGED%\requirements-extra.txt" || goto :fail
) else if exist "%ACTIVE_ADDONS_ROOT%\requirements-extra.txt" (
  "%ODOO_VENV%\Scripts\python.exe" -m pip install -r "%ACTIVE_ADDONS_ROOT%\requirements-extra.txt" || goto :fail
)

call "%SCRIPT_DIR%04_upgrade_modules.bat" "%VLUX_PROFILE%" || goto :fail
call "%SCRIPT_DIR%05_start_and_smoke.bat" || goto :fail

echo [success] Actualizacion VLUX completada: %VLUX_RELEASE%
exit /b 0

:fail_activate
echo ERROR: Fallo activacion de release. Revise staged release y logs. Rollback manual puede ser requerido.
exit /b 91

:fail
echo ERROR: Actualizacion detenida. No se ejecutara rollback destructivo automaticamente.
echo Revise logs y use 06_rollback.bat con backup y release previa concretos si corresponde.
exit /b 92
