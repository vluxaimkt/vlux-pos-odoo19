@echo off
setlocal EnableExtensions EnableDelayedExpansion

call "%~dp0vlux_env.bat" || exit /b 2

echo [preflight] Validando entorno VLUX POS...

if not exist "%ODOO_HOME%\" (
  echo ERROR: ODOO_HOME no existe: %ODOO_HOME%
  exit /b 10
)
if not exist "%ODOO_VENV%\Scripts\python.exe" (
  echo ERROR: Python del venv no existe: %ODOO_VENV%\Scripts\python.exe
  exit /b 11
)
if not exist "%ODOO_CORE%\odoo-bin" (
  echo ERROR: odoo-bin no existe: %ODOO_CORE%\odoo-bin
  exit /b 12
)
if not exist "%ODOO_CONF%" (
  echo ERROR: ODOO_CONF no existe: %ODOO_CONF%
  exit /b 13
)
if "%ODOO_DB%"=="" (
  echo ERROR: ODOO_DB no esta definido.
  exit /b 14
)
if not exist "%PG_BIN%\pg_dump.exe" (
  echo ERROR: pg_dump.exe no existe en PG_BIN: %PG_BIN%
  exit /b 15
)
if not exist "%PG_BIN%\pg_restore.exe" (
  echo ERROR: pg_restore.exe no existe en PG_BIN: %PG_BIN%
  exit /b 16
)

echo CONFIGURED_DATA_DIR=%CONFIGURED_DATA_DIR%
echo DATABASE=%ODOO_DB%
echo EXPECTED_FILESTORE=%EXPECTED_FILESTORE%
echo FILESTORE_EXISTS=%FILESTORE_EXISTS%
echo FILESTORE_HAS_CONTENT=%FILESTORE_HAS_CONTENT%

if "%CONFIGURED_DATA_DIR%"=="" (
  echo ERROR: No se pudo resolver data_dir efectivo desde Odoo.
  exit /b 17
)
if not exist "%CONFIGURED_DATA_DIR%\" (
  echo ERROR: data_dir no existe: %CONFIGURED_DATA_DIR%
  exit /b 18
)
if "%EXPECTED_FILESTORE%"=="" (
  echo ERROR: No se pudo resolver filestore esperado desde Odoo.
  exit /b 19
)
if not exist "%EXPECTED_FILESTORE%\" (
  echo ERROR: filestore esperado no existe: %EXPECTED_FILESTORE%
  exit /b 20
)
if "%FILESTORE_HAS_CONTENT%"=="0" (
  if /I "%VLUX_ENVIRONMENT%"=="production" (
    echo ERROR: filestore vacio en produccion sin estrategia valida.
    exit /b 21
  )
  echo WARNING: filestore existe pero esta vacio; permitido solo en staging/dev.
)

if not exist "%BACKUP_ROOT%\" mkdir "%BACKUP_ROOT%" || exit /b 22
if not exist "%RELEASES_ROOT%\" mkdir "%RELEASES_ROOT%" || exit /b 23
if not exist "%LOG_ROOT%\" mkdir "%LOG_ROOT%" || exit /b 24

"%PG_BIN%\psql.exe" -w -h "%ODOO_DB_HOST%" -p "%ODOO_DB_PORT%" -U "%ODOO_DB_USER%" -d "%ODOO_DB%" -c "select 1" >nul
if errorlevel 1 (
  echo ERROR: No se pudo conectar a PostgreSQL. Configure pgpass.conf o credenciales protegidas.
  exit /b 25
)

for /f %%F in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-PSDrive -Name ([IO.Path]::GetPathRoot('%BACKUP_ROOT%').Substring(0,1))).Free"') do set "FREE_BYTES=%%F"
if "%FREE_BYTES%"=="" (
  echo ERROR: No se pudo validar espacio libre.
  exit /b 26
)
if %FREE_BYTES% LSS 1073741824 (
  echo ERROR: Espacio libre insuficiente en destino de backup.
  exit /b 27
)

if not "%ODOO_SERVICE_NAME%"=="" (
  sc query "%ODOO_SERVICE_NAME%" >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Servicio Odoo configurado pero no encontrado: %ODOO_SERVICE_NAME%
    exit /b 28
  )
)

echo [preflight] OK
exit /b 0
