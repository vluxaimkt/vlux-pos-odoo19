# Validación y pruebas

Esta lista determina si una instalación es utilizable. Ejecútela primero en una
base desechable y después en staging.

## 1. Validación técnica

### Dependencias Python

```powershell
& C:\Odoo\venv\Scripts\python.exe -m pip check
& C:\Odoo\venv\Scripts\python.exe -m pip show qrcode
```

### Versión de Odoo

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  --version

git -C C:\Odoo\src\odoo rev-parse HEAD
```

Resultado de referencia:

```text
Odoo Server 19.0
a2d73c5900d8886d115afe1ccb7f5c97c7e71a97
```

### Instalación limpia

Cree una base desechable llamada `vlux_pos_test` y ejecute:

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  -c C:\Odoo\config\odoo.conf `
  -d vlux_pos_test `
  -i vlux_mobile_scanner,vlux_owner,vlux_facturacion `
  --without-demo `
  --stop-after-init
```

La ejecución debe terminar con código `0` y sin módulos en estado inconsistente.

### Pruebas automatizadas disponibles

Los tres addons incluyen pruebas automatizadas de seguridad y restricciones.
Ejecútelas exclusivamente en una base desechable:

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  -c C:\Odoo\config\odoo.conf `
  -d vlux_pos_test `
  -u vlux_mobile_scanner,vlux_owner,vlux_facturacion `
  --test-enable `
  --test-tags /vlux_mobile_scanner,/vlux_owner,/vlux_facturacion `
  --stop-after-init
```

No ejecute pruebas sobre una base de producción.

### Checks estáticos y paquete de release

Ejecute los checks ligeros del repositorio:

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\custom_addons\vlux-pos-odoo19\scripts\release\static_checks.py
```

Construya la release candidata desde un working tree limpio:

```bat
scripts\release\BUILD_RELEASE.bat 1.0.0-rc1
```

Verifique que se generen:

```text
VLUX_POS_1.0.0-rc1.zip
release-manifest.json
VLUX_POS_1.0.0-rc1.zip.sha256
```

El manifest debe identificar producto, versión, commit fuente, commit Odoo,
Python, PostgreSQL soportado, addons incluidos, versiones de addons y SHA256 del
paquete.

### Dry-run de actualización Windows

Cuando el entorno tenga rutas y credenciales protegidas configuradas, ejecute:

```bat
scripts\windows\00_preflight.bat
scripts\windows\02_stage_release.bat C:\Updates\VLUX_POS_1.0.0-rc1.zip
scripts\windows\03_verify_release.bat C:\Updates\VLUX_POS_1.0.0-rc1.zip
```

No declare exitoso el flujo completo si no se ejecutaron backup, upgrade dirigido
y smoke test contra la DB objetivo.

## 2. Prueba del POS estándar

1. Cree un producto almacenable habilitado para POS.
2. Asigne un código de barras único.
3. Cargue existencias.
4. Abra una caja.
5. Agregue el producto y complete una venta de prueba.
6. Cierre correctamente la sesión.

## 3. Prueba del escáner

1. Abra una nueva sesión POS.
2. Presione **Escáner móvil**.
3. Compruebe que se genera código y URL.
4. Vincule un teléfono.
5. Pruebe captura manual.
6. Pruebe cámara mediante HTTPS.
7. Confirme que el producto aparece una sola vez por lectura.
8. Escanee un código inexistente y verifique un resultado controlado.
9. Revise el historial de eventos.
10. Revoque la conexión y compruebe que el teléfono deja de enviar.

## 4. Prueba de VLUX Owner

1. Cree un usuario propietario de prueba.
2. Asigne el grupo `VLUX Owner`.
3. Confirme que otro usuario sin el grupo recibe acceso denegado.
4. Abra `/vlux-owner/` con el propietario.
5. Compare ventas, tickets y total contra el reporte POS del mismo día.
6. Verifique zona horaria y compañía.
7. Confirme que productos con poca existencia aparecen correctamente.
8. Pruebe el endpoint compartido con una API Key temporal y revóquela después.
9. Confirme que una sesión web sin encabezado Bearer no accede al endpoint.

## 5. Prueba de facturación simulada

1. Confirme modo `Simulación`.
2. Valide la configuración fiscal.
3. Complete una venta POS.
4. Capture datos desde el portal del ticket.
5. Confirme el aviso de documento sin validez fiscal.
6. Verifique la solicitud creada.
7. Descargue el XML de simulación.
8. Confirme que no se genera UUID fiscal ni se presenta como CFDI real.

## 6. Criterios de aceptación local

- Odoo inicia sin errores fatales.
- Los tres addons están instalados.
- El POS puede abrir, vender y cerrar sesión.
- El escáner manual y la cámara HTTPS funcionan.
- El dashboard coincide con datos POS.
- La simulación fiscal queda identificada sin ambigüedad.
- No se usan credenciales o datos de producción.

## 7. Criterios adicionales para producción

- Todas las condiciones de [Producción controlada](PRODUCCION.md) están
  resueltas.
- Existe un respaldo previo verificado.
- Staging ejecutó la misma release y migración.
- Se probó rollback con base y filestore.
- El paquete de release fue verificado por SHA256 y manifest.
- HTTPS, WebSocket, monitoreo y alertas funcionan.
- No hay advertencias propias por APIs obsoletas de Odoo.
