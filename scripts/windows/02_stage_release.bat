@echo off
setlocal EnableExtensions EnableDelayedExpansion

call "%~dp0vlux_env.bat" || exit /b 2
if "%~1"=="" (
  echo Usage: 02_stage_release.bat C:\Updates\VLUX_POS_x.y.z.zip
  exit /b 40
)
set "VLUX_PACKAGE=%~f1"
if not exist "%VLUX_PACKAGE%" (
  echo ERROR: Paquete no existe: %VLUX_PACKAGE%
  exit /b 41
)

for /f "usebackq delims=" %%V in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$z='%VLUX_PACKAGE%'; Add-Type -AssemblyName System.IO.Compression.FileSystem; $zip=[IO.Compression.ZipFile]::OpenRead($z); try { $bad=$zip.Entries | Where-Object { $_.FullName -match '(^/|^[A-Za-z]:|\\.\\.)' }; if($bad){ throw 'ZIP con rutas sospechosas' }; $m=$zip.GetEntry('release-manifest.json'); if(-not $m){ throw 'release-manifest.json faltante' }; $r=New-Object IO.StreamReader($m.Open()); $j=$r.ReadToEnd() | ConvertFrom-Json; $r.Close(); if(-not $j.version){ throw 'version faltante' }; $j.version } finally { $zip.Dispose() }"`) do set "VLUX_RELEASE=%%V"
if errorlevel 1 exit /b 42
if "%VLUX_RELEASE%"=="" (
  echo ERROR: No se pudo determinar version del paquete.
  exit /b 43
)

set "STAGED=%RELEASES_ROOT%\%VLUX_RELEASE%"
if exist "%STAGED%\" rmdir /s /q "%STAGED%"
mkdir "%STAGED%" || exit /b 44

powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%VLUX_PACKAGE%' -DestinationPath '%STAGED%' -Force"
if errorlevel 1 (
  echo ERROR: No se pudo extraer el paquete.
  exit /b 45
)
if not exist "%STAGED%\release-manifest.json" (
  echo ERROR: Manifest no encontrado en release staged.
  exit /b 46
)
if not exist "%STAGED%\addons\vlux_mobile_scanner\__manifest__.py" exit /b 47
if not exist "%STAGED%\addons\vlux_owner\__manifest__.py" exit /b 48
if not exist "%STAGED%\addons\vlux_facturacion\__manifest__.py" exit /b 49

echo %STAGED%>"%ODOO_HOME%\last_vlux_staged.txt"
echo [stage] OK: %STAGED%
exit /b 0
