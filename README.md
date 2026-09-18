# VLUX POS para Odoo 19

Repositorio de extensiones para operar un Punto de Venta VLUX sobre Odoo 19
Community. No es una aplicación autónoma: requiere Odoo, PostgreSQL y una base
de datos inicializada.

## Alcance

El sistema POS está compuesto por estos addons:

| Addon | Función | Dependencias Odoo |
| --- | --- | --- |
| `vlux_core` | Roles VLUX, metadatos de edición, health check y diagnóstico base | `point_of_sale`, `stock`, `product` |
| `vlux_mobile_scanner` | Convierte un teléfono en lector inalámbrico del POS | `point_of_sale`, `bus`, `web` |
| `vlux_owner` | Dashboard móvil de ventas, cajas, productos e inventario | `vlux_core`, `point_of_sale`, `stock`, `web` |
| `vlux_pos_catalog` | Alta rápida de productos desde el POS al escanear un código desconocido | `point_of_sale`, `stock`, `vlux_core` |
| `vlux_facturacion` | Portal y flujo de facturación simulada desde tickets POS | `account`, `mail`, `point_of_sale`, `l10n_mx` |

`vlux_facturacion` no emite CFDI real. No incluye timbrado, cancelación, CSD ni
conexión con un PAC productivo. Debe mantenerse en modo `simulation`.

## Versiones de referencia

La instalación validada actualmente utiliza:

| Componente | Versión |
| --- | --- |
| Odoo Community | `19.0` |
| Commit Odoo | `a2d73c5900d8886d115afe1ccb7f5c97c7e71a97` |
| Python | `3.12.10` |
| PostgreSQL | `16.14` |
| `qrcode` | `7.4.2` |
| wkhtmltopdf | `0.12.6` con Qt parcheado |

El commit de Odoo se fija para que dos instalaciones usen el mismo núcleo. Un
checkout posterior de la rama mutable `19.0` debe validarse antes de desplegarse.

## Inicio rápido

En una instalación que ya tenga Odoo 19 y PostgreSQL configurados:

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  -c C:\Odoo\config\odoo.conf `
  -d vlux_pos_dev `
  -i vlux_core,vlux_mobile_scanner,vlux_owner,vlux_facturacion `
  --without-demo `
  --stop-after-init
```

Después se inicia el servidor:

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  -c C:\Odoo\config\odoo.conf `
  -d vlux_pos_dev
```

Dirección local:

```text
http://127.0.0.1:8069/web?db=vlux_pos_dev
```

La instalación completa desde una computadora limpia está descrita en
[`docs/INSTALACION_WINDOWS.md`](docs/INSTALACION_WINDOWS.md).

## Código, base y filestore

Git contiene únicamente código. La operación completa está dividida en:

| Elemento | Contenido | Se guarda en Git |
| --- | --- | --- |
| Repositorio POS | Addons y documentación | Sí |
| Odoo core | Plataforma oficial | No; se clona por separado |
| PostgreSQL | Usuarios, productos, ventas, sesiones y configuración | No |
| Filestore | Adjuntos, imágenes y documentos generados | No |
| `odoo.conf` | Rutas y credenciales del entorno | No |

Para reproducir datos existentes se necesitan un dump PostgreSQL y el filestore
correspondiente a la misma base. Para una instalación nueva se crea una base
vacía y se realiza la configuración funcional desde cero.

## Releases para cliente

GitHub privado es fuente interna de VLUX. Los equipos de cliente no deben tener
tokens, deploy keys ni ejecutar `git pull` o `git fetch`.

VLUX distribuye paquetes `VLUX_POS_<version>.zip` acompañados por
`release-manifest.json` y `SHA256`. El manifest registra producto, versión,
commit fuente, baseline Odoo, Python, PostgreSQL soportado, addons incluidos,
versiones de addons y hash del paquete. La firma digital queda preparada como
punto de extensión, pero no se declara configurada hasta contar con
infraestructura real de firma.

## Documentación

- [Arquitectura](docs/ARQUITECTURA.md)
- [Decisión: Odoo como backend, interfaces propias](docs/ARQUITECTURA_HEADLESS.md)
- [Instalación en Windows](docs/INSTALACION_WINDOWS.md)
- [Configuración funcional](docs/CONFIGURACION_FUNCIONAL.md)
- [Operación diaria](docs/OPERACION.md)
- [Validación y pruebas](docs/VALIDACION.md)
- [Distribución de producción](docs/DISTRIBUCION_PRODUCCION.md)
- [Producción controlada](docs/PRODUCCION.md)
- [Respaldo y restauración](docs/RESPALDO_RESTAURACION.md)
- [Operabilidad y rendimiento](docs/OPERABILIDAD_PERFORMANCE.md)

## Estado operativo

Los tres addons están instalados en el entorno de desarrollo existente. El 10 de
septiembre de 2026 se validaron una instalación conjunta en una base vacía y una
migración sobre una copia exacta del respaldo. Las doce pruebas automatizadas de
los tres addons terminaron con `0 failed, 0 errors`.

Esta comprobación valida instalación y pruebas básicas, pero no sustituye la
validación funcional completa ni una autorización de producción.

Antes de producción deben cumplirse, como mínimo, estas condiciones:

1. Completar la lista de validación funcional en staging.
2. Ejecutar Odoo como servicio detrás de un proxy HTTPS.
3. Fijar `dbfilter`, deshabilitar la lista de bases y rotar credenciales.
4. Probar respaldo y restauración de base y filestore en staging.
5. Configurar monitoreo, alertas y retención de logs.
6. Desplegar únicamente tags o commits aprobados.
7. Mantener `vlux_facturacion` identificado como simulador sin validez fiscal.

## Seguridad

- `vlux_core` define los roles funcionales VLUX: Owner, Administrator,
  Supervisor, Cashier, Inventory Operator, Auditor y Support. Reutiliza grupos
  nativos de Odoo cuando son seguros y añade controles servidor donde el permiso
  nativo es demasiado amplio, por ejemplo bloqueo de `pos.config` para Cashier.
- Nunca suba `odoo.conf`, `.env`, dumps, filestore, logs ni credenciales.
- El repositorio es público: cada commit debe asumirse visible. Nunca versione datos reales.
- Use API Keys de Odoo para el endpoint compartido de Owner y revoque las que no
  estén en uso.
- No publique directamente el puerto `8069` en Internet.
- Rote secretos por ambiente y después de cualquier exposición.
