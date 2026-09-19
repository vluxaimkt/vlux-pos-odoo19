@echo off
setlocal EnableExtensions EnableDelayedExpansion

call "%~dp0vlux_env.bat" || exit /b 2
if "%~3"=="" (
  echo Usage: 06_rollback.bat C:\Backups\yyyyMMdd_HHmmss previous_release CONFIRM
  echo Rollback restaura DB + filestore + release previa. No se ejecuta automaticamente.
  exit /b 80
)
if /I not "%~3"=="CONFIRM" (
  echo ERROR: Confirmacion requerida: CONFIRM
  exit /b 81
)
set "BACKUP_DIR=%~f1"
set "PREVIOUS_RELEASE=%~2"
set "PREVIOUS_DIR=%RELEASES_ROOT%\%PREVIOUS_RELEASE%"
if not exist "%BACKUP_DIR%\" exit /b 82
if exist "%BACKUP_DIR%\INVALID_BACKUP.txt" (
  echo ERROR: Backup marcado invalido: %BACKUP_DIR%
  exit /b 83
)
"%ODOO_VENV%\Scripts\python.exe" "%~dp0odoo_env.py" validate-backup --backup-dir "%BACKUP_DIR%" --db "%ODOO_DB%"
if errorlevel 1 exit /b 84
if not exist "%PREVIOUS_DIR%\addons\" exit /b 84

if not "%ODOO_SERVICE_NAME%"=="" (
  sc stop "%ODOO_SERVICE_NAME%"
) else if exist "%ODOO_LOCAL_PID_FILE%" (
  for /f "usebackq delims=" %%P in ("%ODOO_LOCAL_PID_FILE%") do powershell -NoProfile -Command "Stop-Process -Id %%P -ErrorAction SilentlyContinue"
)

"%PG_BIN%\pg_restore.exe" -w -h "%ODOO_DB_HOST%" -p "%ODOO_DB_PORT%" -U "%ODOO_DB_USER%" --clean --if-exists --no-owner --no-privileges -d "%ODOO_DB%" "%BACKUP_DIR%\database.dump"
if errorlevel 1 (
  echo ERROR: Restauracion PostgreSQL fallo.
  exit /b 85
)

if exist "%EXPECTED_FILESTORE%\" rmdir /s /q "%EXPECTED_FILESTORE%"
robocopy "%BACKUP_DIR%\filestore\%ODOO_DB%" "%EXPECTED_FILESTORE%" /E /R:2 /W:2 /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 exit /b 86

robocopy "%PREVIOUS_DIR%\addons" "%ACTIVE_ADDONS_ROOT%" /E /R:2 /W:2 /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 exit /b 87
echo %PREVIOUS_RELEASE%>"%ODOO_HOME%\vlux_current_release.txt"

call "%~dp005_start_and_smoke.bat"
exit /b %ERRORLEVEL%
