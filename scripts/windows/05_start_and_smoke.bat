@echo off
setlocal EnableExtensions

call "%~dp0vlux_env.bat" || exit /b 2

if not "%ODOO_SERVICE_NAME%"=="" (
  sc start "%ODOO_SERVICE_NAME%" >nul 2>nul
) else (
  if not exist "%ODOO_HOME%\run\" mkdir "%ODOO_HOME%\run"
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$pidFile='%ODOO_LOCAL_PID_FILE%'; $running=$false; if(Test-Path $pidFile){ $pidText=(Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1); if($pidText){ $p=Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue; if($p){ $running=$true } } }; if(-not $running){ $p=Start-Process -FilePath '%ODOO_VENV%\Scripts\python.exe' -ArgumentList @('%ODOO_CORE%\odoo-bin','-c','%ODOO_CONF%','-d','%ODOO_DB%','--logfile','%ODOO_LOG%') -PassThru -WindowStyle Hidden; $p.Id | Set-Content -Encoding ASCII -LiteralPath $pidFile }"
  if errorlevel 1 (
    echo ERROR: No se pudo iniciar Odoo local.
    exit /b 69
  )
)

echo [smoke] Esperando HTTP %ODOO_URL% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$url='%ODOO_URL%'; $ok=$false; for($i=0;$i -lt 30;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 500){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 2 }; if(-not $ok){ throw 'Odoo no respondio por HTTP' }"
if errorlevel 1 exit /b 70

"%PG_BIN%\psql.exe" -w -h "%ODOO_DB_HOST%" -p "%ODOO_DB_PORT%" -U "%ODOO_DB_USER%" -d "%ODOO_DB%" -c "select 1" >nul
if errorlevel 1 (
  echo ERROR: DB no responde.
  exit /b 71
)

"%ODOO_VENV%\Scripts\python.exe" "%ODOO_CORE%\odoo-bin" shell -c "%ODOO_CONF%" -d "%ODOO_DB%" --no-http < "%~dp0smoke_check.py"
if errorlevel 1 (
  echo ERROR: Smoke funcional fallo.
  exit /b 72
)

if exist "%ODOO_LOG%" (
  powershell -NoProfile -Command "$fatal=Select-String -Path '%ODOO_LOG%' -Pattern 'CRITICAL|Traceback|FATAL' -SimpleMatch -ErrorAction SilentlyContinue | Select-Object -Last 5; if($fatal){ $fatal; exit 1 }"
  if errorlevel 1 (
    echo ERROR: Se encontraron errores fatales recientes en el log.
    exit /b 73
  )
)

echo [smoke] OK
exit /b 0
