@echo off
setlocal EnableExtensions EnableDelayedExpansion

call "%~dp0vlux_env.bat" || exit /b 2

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%T"
set "BACKUP_DIR=%BACKUP_ROOT%\%TS%"
set "DB_DUMP=%BACKUP_DIR%\database.dump"
set "BACKUP_MANIFEST=%BACKUP_DIR%\backup-manifest.json"
set "CHECKSUMS=%BACKUP_DIR%\checksums.txt"
set "DUMP_CATALOG=%BACKUP_DIR%\pg_restore_catalog.txt"

echo [backup] Creando respaldo en %BACKUP_DIR%
mkdir "%BACKUP_DIR%" || exit /b 30
mkdir "%BACKUP_DIR%\filestore" || exit /b 31

"%PG_BIN%\pg_dump.exe" -w -h "%ODOO_DB_HOST%" -p "%ODOO_DB_PORT%" -U "%ODOO_DB_USER%" -d "%ODOO_DB%" -Fc --file "%DB_DUMP%"
if errorlevel 1 (
  echo ERROR: pg_dump fallo. Abortando.
  exit /b 32
)
if not exist "%DB_DUMP%" (
  echo ERROR: No se genero database.dump.
  exit /b 33
)
for %%F in ("%DB_DUMP%") do if %%~zF LEQ 0 (
  echo ERROR: database.dump esta vacio.
  exit /b 34
)

robocopy "%EXPECTED_FILESTORE%" "%BACKUP_DIR%\filestore\%ODOO_DB%" /E /R:2 /W:2 /NFL /NDL /NP
if %ERRORLEVEL% GEQ 8 (
  echo ERROR: Fallo la copia del filestore.
  exit /b 35
)

"%PG_BIN%\pg_restore.exe" --list "%DB_DUMP%" > "%DUMP_CATALOG%"
if errorlevel 1 (
  echo ERROR: pg_restore --list no pudo leer database.dump.
  exit /b 36
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dump='%DB_DUMP%'; $fs='%BACKUP_DIR%\filestore\%ODOO_DB%'; $sourceFs='%EXPECTED_FILESTORE%'; $manifest=[ordered]@{db='%ODOO_DB%';timestamp='%TS%';release=$env:VLUX_CURRENT_RELEASE;database_dump=$dump;database_size_bytes=(Get-Item $dump).Length;source_filestore_path=$sourceFs;filestore_path=$fs;configured_data_dir='%CONFIGURED_DATA_DIR%'}; $manifest | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 '%BACKUP_MANIFEST%'; $s=[IO.File]::OpenRead($dump); try { $sha=[BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash($s)).Replace('-','').ToLowerInvariant() } finally { $s.Dispose() }; \"$sha  database.dump\" | Set-Content -Encoding ASCII '%CHECKSUMS%'"
if errorlevel 1 (
  echo ERROR: No se pudo generar manifest/checksum de backup.
  exit /b 37
)

"%ODOO_VENV%\Scripts\python.exe" "%~dp0odoo_env.py" validate-backup --backup-dir "%BACKUP_DIR%" --db "%ODOO_DB%"
if errorlevel 1 (
  echo ERROR: Validacion de backup fallo.
  exit /b 38
)

echo %BACKUP_DIR%>"%ODOO_HOME%\last_vlux_backup.txt"
echo [backup] OK: %BACKUP_DIR%
exit /b 0
