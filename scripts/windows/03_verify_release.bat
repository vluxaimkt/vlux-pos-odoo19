@echo off
setlocal EnableExtensions

call "%~dp0vlux_env.bat" || exit /b 2
if "%~1"=="" (
  echo Usage: 03_verify_release.bat C:\Updates\VLUX_POS_x.y.z.zip
  exit /b 50
)
set "VLUX_PACKAGE=%~f1"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$zip='%VLUX_PACKAGE%'; $expected='%EXPECTED_SHA256%'; $odoo='%ODOO_BASELINE_COMMIT%'; $addons=@('vlux_mobile_scanner','vlux_owner','vlux_pos_catalog','vlux_facturacion'); Add-Type -AssemblyName System.IO.Compression.FileSystem; $s=[IO.File]::OpenRead($zip); try { $sha=[BitConverter]::ToString(([Security.Cryptography.SHA256]::Create()).ComputeHash($s)).Replace('-','').ToLowerInvariant() } finally { $s.Dispose() }; if(-not $sha){ throw 'No se pudo calcular SHA256' }; if($expected -and $sha -ne $expected.ToLowerInvariant()){ throw 'SHA256 no coincide' }; $archive=[IO.Compression.ZipFile]::OpenRead($zip); try { $m=$archive.GetEntry('release-manifest.json'); if(-not $m){ throw 'manifest faltante' }; $r=[IO.StreamReader]::new($m.Open()); $j=$r.ReadToEnd() | ConvertFrom-Json; $r.Close(); if($j.product -ne 'VLUX POS'){ throw 'producto invalido' }; if($j.odoo_commit -ne $odoo){ throw 'baseline Odoo invalido' }; foreach($a in $addons){ if($j.included_addons -notcontains $a){ throw \"addon faltante $a\" }; if(-not $j.addon_versions.$a){ throw \"version faltante $a\" }; if(-not $archive.GetEntry(\"addons/$a/__manifest__.py\")){ throw \"estructura faltante $a\" } }; if($j.signature.status -eq 'not_configured'){ Write-Host '[verify] Firma digital pendiente de infraestructura; validacion por SHA256.' }; Write-Host \"[verify] SHA256 $sha\" } finally { $archive.Dispose() }"
if errorlevel 1 (
  echo ERROR: Verificacion de release fallida.
  exit /b 51
)

echo [verify] OK
exit /b 0
