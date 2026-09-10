# Configuración funcional

Complete esta guía después de instalar los addons. Los nombres de menús pueden
variar ligeramente según el idioma y los permisos del usuario.

## 1. Preparar Odoo POS

Configure primero los elementos estándar de Odoo:

1. Registre nombre, zona horaria, moneda y datos de la compañía.
2. Cree almacén y ubicaciones de inventario.
3. Cree los usuarios de caja y asigne permisos de Punto de Venta.
4. Configure diarios y métodos de pago.
5. Cree una configuración de Punto de Venta.
6. Habilite productos para POS y capture sus códigos de barras.
7. Cargue existencias para los productos almacenables.
8. Abra una sesión y realice una venta de prueba.

Los addons consumen estos datos estándar; no crean automáticamente catálogo,
inventario, métodos de pago ni usuarios.

## 2. Escáner móvil

### Requisitos

- Una sesión POS abierta.
- Productos con código de barras único.
- Teléfono con navegador compatible.
- Acceso del teléfono a la URL de Odoo.
- HTTPS para utilizar la cámara. En HTTP solo funciona el envío manual.

### URL pública

Active el modo desarrollador y abra:

```text
Ajustes > Técnico > Parámetros > Parámetros del sistema
```

Cree o ajuste:

| Clave | Ejemplo | Uso |
| --- | --- | --- |
| `vlux_mobile_scanner.base_url` | `https://pos.example.com` | URL que abrirá el teléfono |

No agregue rutas como `/web` ni una diagonal final. Si el parámetro está vacío,
el addon intenta usar el host actual y después `web.base.url`.

### Vincular el teléfono

1. Abra la caja POS.
2. Presione **Escáner móvil** o **Conectar escáner móvil**.
3. Abra en el teléfono la URL mostrada por la caja.
4. Capture el código temporal de ocho caracteres.
5. Autorice la cámara cuando el navegador lo solicite.
6. Escanee un producto y verifique que aparezca en la orden activa.

El código temporal vence después de diez minutos. El token del teléfono vence
después de ocho horas, al cerrar la sesión POS o al revocar la conexión.

### Historial

Los responsables de POS pueden consultar:

```text
Punto de Venta > Productos > VLUX Mobile Scanner
```

El historial muestra caja, sesión, código, producto y resultado. Los eventos y
emparejamientos terminados se eliminan automáticamente después de treinta días.

## 3. VLUX Owner

### Habilitar un propietario

1. Abra `Ajustes > Usuarios y compañías > Usuarios`.
2. Seleccione el usuario propietario.
3. Asigne el grupo `VLUX Owner`.
4. Configure correctamente la compañía y zona horaria del usuario.
5. Inicie sesión con ese usuario.
6. Abra `/vlux-owner/`.

Dirección local de ejemplo:

```text
http://127.0.0.1:8069/vlux-owner/
```

El dashboard muestra ventas del día, comparación, cajas, productos, ventas
recientes y existencias bajas. Solo toma órdenes en estado `paid`, `done` o
`invoiced` de la compañía activa.

### Parámetro opcional

| Clave | Predeterminado | Descripción |
| --- | --- | --- |
| `vlux_owner.low_stock_threshold` | `10` | Umbral para existencias bajas |

Para el acceso normal autenticado solo se necesita el grupo `VLUX Owner`.

Para una integración externa, cree un usuario técnico en la compañía correcta,
asígnele únicamente el grupo `VLUX Owner` y genere una API Key desde las
preferencias de ese usuario. Use exclusivamente HTTPS, registre responsable y
fecha de expiración, y revoque la clave cuando deje de utilizarse.

El endpoint compartido es:

```text
POST /vlux_owner/api/share/dashboard
Authorization: Bearer <api-key-de-odoo>
Content-Type: application/json

{"date": "2026-08-31"}
```

El resultado se calcula para el usuario y la compañía asociados a la API Key.
No incluya la clave en documentación, código, capturas o repositorios.

## 4. Facturación simulada

### Restricción funcional

Este módulo produce una simulación sin validez fiscal. No debe ofrecerse como
CFDI ni configurarse como facturación real.

### Configurar simulador

Un administrador abre:

```text
VLUX Facturación > Configuración fiscal
```

La instalación crea una configuración para la compañía principal y el proveedor
`VLUX PAC Simulador`.

1. Mantenga `Modo de operación` en `Simulación`.
2. Seleccione `VLUX PAC Simulador`.
3. Presione **Cargar datos de empresa**.
4. Complete al menos el nombre del emisor.
5. Presione **Probar proveedor**.
6. Presione **Validar configuración**.
7. Mantenga claro para el usuario que el XML no tiene validez fiscal.

No cambie a `Producción`. El código no implementa PAC real, CSD, timbrado ni
cancelación.

### Flujo de prueba

1. Complete una venta POS.
2. Abra el portal de validación del ticket proporcionado por Odoo.
3. Capture los datos solicitados.
4. Confirme que se trata de una simulación.
5. Verifique el resultado en `VLUX Facturación > Solicitudes`.
6. Descargue el XML y confirme que está marcado como simulación.

## 5. Parámetros por ambiente

Registre valores distintos en cada base:

| Parámetro | Desarrollo | Staging | Producción |
| --- | --- | --- | --- |
| URL del escáner | URL local o túnel controlado | Dominio staging | Dominio HTTPS productivo |
| API Key Owner | Clave de usuario técnico local | Clave exclusiva de staging | Clave exclusiva y rotada |
| Datos fiscales | Ficticios | Sanitizados | Simulación; no CFDI real |

No copie parámetros secretos de producción a desarrollo.
