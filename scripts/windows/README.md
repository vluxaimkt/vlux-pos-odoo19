# Actualización Windows VLUX POS

Estos scripts actualizan instalaciones locales desde paquetes `VLUX_POS_<version>.zip`.
No usan `git pull`, tokens de GitHub ni acceso al historial Git en equipos de cliente.

## Variables

Configure variables no secretas antes de ejecutar, si las rutas difieren:

```bat
set ODOO_HOME=C:\Odoo
set ODOO_VENV=C:\Odoo\venv
set ODOO_CONF=C:\Odoo\config\odoo.conf
set ODOO_DB=vlux_pos_prod
set PG_BIN=C:\Program Files\PostgreSQL\16\bin
set ODOO_SERVICE_NAME=odoo-vlux
set BACKUP_ROOT=D:\OdooBackups
set VLUX_PROFILE=scanner_owner
```

No hardcodee contraseñas PostgreSQL. Use `pgpass.conf`, variables protegidas del
entorno operativo o el mecanismo seguro aprobado para el cliente.

## Flujo normal

```bat
VLUX_UPDATE.bat C:\Updates\VLUX_POS_1.0.1.zip
```

El flujo ejecuta:

1. `00_preflight.bat`
2. `01_backup.bat`
3. `02_stage_release.bat`
4. `03_verify_release.bat`
5. activación de addons desde la release staged
6. instalación de dependencias declaradas
7. `04_upgrade_modules.bat`
8. `05_start_and_smoke.bat`

Ante un error, el flujo se detiene y conserva logs/backup. No restaura de forma
destructiva automáticamente.

## Perfiles de módulos

- `scanner`: actualiza `vlux_mobile_scanner`
- `owner`: actualiza `vlux_owner`
- `scanner_owner`: actualiza ambos
- `facturacion_internal`: actualiza sólo `vlux_facturacion`
- `custom`: usa `VLUX_MODULES`

No use `-u all`. Facturación es simulador y no se instala en todos los clientes.

## Rollback manual

Rollback requiere backup, release previa y confirmación explícita:

```bat
06_rollback.bat D:\OdooBackups\20260913_010203 1.0.0 CONFIRM
```

Restaura PostgreSQL, filestore y código de la release previa. Nunca combine una
base migrada con código viejo.

## Firma digital futura

`03_verify_release.bat` valida SHA256 y manifest. El manifest deja campo de
extensión para firma digital cuando VLUX tenga infraestructura de firma.
