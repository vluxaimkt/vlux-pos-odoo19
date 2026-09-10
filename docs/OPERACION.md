# Operación diaria

## Iniciar en desarrollo

Compruebe PostgreSQL:

```powershell
Get-Service postgresql-x64-16
Start-Service postgresql-x64-16
```

Inicie Odoo en primer plano:

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  -c C:\Odoo\config\odoo.conf `
  -d vlux_pos_dev
```

Abra:

```text
http://127.0.0.1:8069/web?db=vlux_pos_dev
```

## Detener en desarrollo

Use `Ctrl+C` en la consola que ejecuta Odoo. Si el proceso quedó separado,
identifique únicamente procesos cuyo comando contenga el `odoo-bin` esperado:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'C:\\Odoo\\src\\odoo\\odoo-bin' } |
  Select-Object ProcessId, Name, CommandLine
```

Después detenga el PID confirmado:

```powershell
Stop-Process -Id <PID>
```

No termine todos los procesos `python.exe`, porque pueden pertenecer a otras
aplicaciones.

## Comprobación de salud

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8069/web/login `
  -UseBasicParsing `
  -TimeoutSec 15
```

El resultado esperado es HTTP `200`. Compruebe también el listener:

```powershell
Get-NetTCPConnection -LocalPort 8069 -State Listen
```

## Apertura y cierre de caja

Al comenzar:

1. Inicie sesión con el usuario de caja.
2. Abra la sesión POS y capture el control de apertura.
3. Verifique métodos de pago, catálogo y conectividad.
4. Vincule el teléfono únicamente después de abrir la sesión.
5. Realice una lectura de prueba.

Al terminar:

1. Revise órdenes pendientes o con error.
2. Desvincule el teléfono.
3. Cierre la sesión POS mediante el flujo de Odoo.
4. Complete el control de cierre.
5. Revise eventos fallidos del escáner.

## Actualizar addons

En desarrollo puede actualizar los tres addons con:

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  -c C:\Odoo\config\odoo.conf `
  -d vlux_pos_dev `
  -u vlux_mobile_scanner,vlux_owner,vlux_facturacion `
  --stop-after-init
```

Reinicie Odoo después de una actualización. En producción siga el procedimiento
de [Producción controlada](PRODUCCION.md), incluyendo respaldo y rollback.

## Logs

La ruta depende de `logfile` en `odoo.conf`. En la estructura recomendada:

```text
C:\Odoo\logs\odoo.log
```

Consulte actividad reciente:

```powershell
Get-Content C:\Odoo\logs\odoo.log -Tail 100
```

Los logs pueden contener nombres de usuario, referencias de venta y tokens en
URLs. Restrinja permisos y no los publique.

## Diagnóstico

### Odoo no abre

1. Confirme que PostgreSQL está activo.
2. Confirme que `8069` no está ocupado por otro proceso.
3. Revise credenciales y rutas de `odoo.conf`.
4. Revise las últimas líneas del log.
5. Ejecute Odoo en primer plano para observar el error.

### Un addon no aparece

1. Confirme que la raíz del repositorio está en `addons_path`.
2. Reinicie Odoo.
3. Active modo desarrollador y actualice la lista de aplicaciones.
4. Verifique el nombre técnico del addon.
5. Revise dependencias faltantes en el log.

### El escáner no conecta

1. Confirme que la sesión POS está abierta.
2. Genere un código nuevo si pasaron diez minutos.
3. Compruebe que el teléfono puede abrir la URL configurada.
4. Use HTTPS para la cámara.
5. Confirme que firewall y proxy permiten las rutas `/vlux/mobile/*`.
6. Revise el historial del escáner y el log de Odoo.

### El código no agrega producto

1. Confirme que el código está asignado a un producto único.
2. Confirme que el producto está habilitado y cargado en el POS.
3. Recargue los datos de la caja.
4. Revise si el evento quedó como `not_found` o `failed`.

### VLUX Owner niega acceso

1. Confirme que el usuario inició sesión.
2. Asigne el grupo `VLUX Owner`.
3. Cierre sesión y vuelva a entrar.
4. Confirme compañía y zona horaria.

### La simulación fiscal no procesa

1. Mantenga el modo `Simulación`.
2. Confirme que la configuración está activa y validada.
3. Confirme el proveedor `VLUX PAC Simulador`.
4. Revise `VLUX Facturación > Solicitudes` y el log.
5. No intente cargar CSD ni activar un PAC real.
