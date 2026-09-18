@echo off
setlocal EnableExtensions EnableDelayedExpansion

call "%~dp0vlux_env.bat" || exit /b 2
set "PROFILE=%~1"
if "%PROFILE%"=="" set "PROFILE=%VLUX_PROFILE%"

if /I "%PROFILE%"=="scanner" set "MODULES=vlux_mobile_scanner"
if /I "%PROFILE%"=="owner" set "MODULES=vlux_owner"
if /I "%PROFILE%"=="scanner_owner" set "MODULES=vlux_mobile_scanner,vlux_owner,vlux_pos_catalog"
if /I "%PROFILE%"=="catalog" set "MODULES=vlux_pos_catalog"
if /I "%PROFILE%"=="facturacion_internal" set "MODULES=vlux_facturacion"
if /I "%PROFILE%"=="custom" set "MODULES=%VLUX_MODULES%"

if "%MODULES%"=="" (
  echo ERROR: Perfil invalido o VLUX_MODULES vacio: %PROFILE%
  exit /b 60
)

set "LOG_FILE=%LOG_ROOT%\upgrade_%PROFILE%_%DATE:/=-%_%TIME::=-%.log"
set "LOG_FILE=%LOG_FILE: =_%"
echo [upgrade] Actualizando modulos: %MODULES%

rem -i instala los modulos que una version nueva agrego (p. ej. vlux_pos_catalog);
rem para los ya instalados no hace nada y -u los actualiza.
"%ODOO_VENV%\Scripts\python.exe" "%ODOO_CORE%\odoo-bin" -c "%ODOO_CONF%" -d "%ODOO_DB%" -i "%MODULES%" -u "%MODULES%" --stop-after-init --logfile "%LOG_FILE%"
if errorlevel 1 (
  echo ERROR: Upgrade fallo. Log: %LOG_FILE%
  exit /b 61
)

echo [upgrade] OK
exit /b 0
