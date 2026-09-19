@echo off
setlocal EnableExtensions

if "%ODOO_HOME%"=="" set "ODOO_HOME=C:\Odoo"
if "%ODOO_CORE%"=="" set "ODOO_CORE=%ODOO_HOME%\src\odoo"
if "%ODOO_VENV%"=="" set "ODOO_VENV=%ODOO_HOME%\venv"
if "%ODOO_CONF%"=="" set "ODOO_CONF=%ODOO_HOME%\config\odoo.conf"
if "%ODOO_DB%"=="" set "ODOO_DB=vlux_pos_prod"
if "%PG_BIN%"=="" set "PG_BIN=C:\Program Files\PostgreSQL\16\bin"
if "%BACKUP_ROOT%"=="" set "BACKUP_ROOT=%ODOO_HOME%\backups"
if "%FILESTORE_ROOT%"=="" set "FILESTORE_ROOT=%ODOO_HOME%\data\filestore"
if "%RELEASES_ROOT%"=="" set "RELEASES_ROOT=%ODOO_HOME%\vlux_releases"
if "%ACTIVE_ADDONS_ROOT%"=="" set "ACTIVE_ADDONS_ROOT=%ODOO_HOME%\custom_addons"
if "%LOG_ROOT%"=="" set "LOG_ROOT=%ODOO_HOME%\logs"
if "%ODOO_URL%"=="" set "ODOO_URL=http://127.0.0.1:8069/web"
if "%ODOO_LOG%"=="" set "ODOO_LOG=%LOG_ROOT%\odoo.log"
if "%ODOO_LOCAL_PID_FILE%"=="" set "ODOO_LOCAL_PID_FILE=%ODOO_HOME%\run\vlux_odoo.pid"
if "%VLUX_PROFILE%"=="" set "VLUX_PROFILE=scanner_owner"
if "%ODOO_BASELINE_COMMIT%"=="" set "ODOO_BASELINE_COMMIT=a2d73c5900d8886d115afe1ccb7f5c97c7e71a97"

if exist "%ODOO_HOME%\vlux_current_release.txt" (
  for /f "usebackq delims=" %%R in ("%ODOO_HOME%\vlux_current_release.txt") do set "VLUX_CURRENT_RELEASE=%%R"
)

set "VLUX_ENV_TMP=%TEMP%\vlux_odoo_env_%ODOO_DB%_%RANDOM%_%RANDOM%_%RANDOM%.bat"
"%ODOO_VENV%\Scripts\python.exe" "%~dp0odoo_env.py" env --odoo-core "%ODOO_CORE%" --config "%ODOO_CONF%" --db "%ODOO_DB%" --format bat > "%VLUX_ENV_TMP%"
if errorlevel 1 (
  if exist "%VLUX_ENV_TMP%" del "%VLUX_ENV_TMP%"
  exit /b 3
)
call "%VLUX_ENV_TMP%"
if exist "%VLUX_ENV_TMP%" del "%VLUX_ENV_TMP%" >nul 2>nul

endlocal & (
  set "ODOO_HOME=%ODOO_HOME%"
  set "ODOO_CORE=%ODOO_CORE%"
  set "ODOO_VENV=%ODOO_VENV%"
  set "ODOO_CONF=%ODOO_CONF%"
  set "ODOO_DB=%ODOO_DB%"
  set "PG_BIN=%PG_BIN%"
  set "BACKUP_ROOT=%BACKUP_ROOT%"
  set "FILESTORE_ROOT=%FILESTORE_ROOT%"
  set "RELEASES_ROOT=%RELEASES_ROOT%"
  set "ACTIVE_ADDONS_ROOT=%ACTIVE_ADDONS_ROOT%"
  set "LOG_ROOT=%LOG_ROOT%"
  set "ODOO_URL=%ODOO_URL%"
  set "ODOO_LOG=%ODOO_LOG%"
  set "ODOO_LOCAL_PID_FILE=%ODOO_LOCAL_PID_FILE%"
  set "VLUX_PROFILE=%VLUX_PROFILE%"
  set "ODOO_BASELINE_COMMIT=%ODOO_BASELINE_COMMIT%"
  set "VLUX_CURRENT_RELEASE=%VLUX_CURRENT_RELEASE%"
  set "CONFIGURED_DATA_DIR=%CONFIGURED_DATA_DIR%"
  set "ODOO_DB_HOST=%ODOO_DB_HOST%"
  set "ODOO_DB_PORT=%ODOO_DB_PORT%"
  set "ODOO_DB_USER=%ODOO_DB_USER%"
  set "EXPECTED_FILESTORE=%EXPECTED_FILESTORE%"
  set "FILESTORE_EXISTS=%FILESTORE_EXISTS%"
  set "FILESTORE_HAS_CONTENT=%FILESTORE_HAS_CONTENT%"
)
exit /b 0
