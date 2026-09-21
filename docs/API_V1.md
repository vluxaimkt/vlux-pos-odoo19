# API VLUX v1

Contrato estable entre las interfaces VLUX y Odoo. Es la base de la
[Iniciativa 2](PLAN_INICIATIVAS.md) y del camino descrito en
[ARQUITECTURA_HEADLESS.md](ARQUITECTURA_HEADLESS.md): las pantallas llaman, el
servidor decide.

Raíz: `https://<tienda>/vlux/api/v1`

## 1. Regla de compatibilidad

`v1` **nunca** cambia de forma que rompa a un cliente ya instalado en una
tienda. Se pueden añadir campos y endpoints; quitar o cambiar el significado de
algo existente obliga a `v2`, y ambas versiones conviven mientras haya cajas en
campo. Cada respuesta lleva la cabecera `X-Vlux-Api-Version`.

## 2. Forma de las respuestas

```json
{"ok": true,  "data": { ... },                          "request_id": "8f3c..."}
{"ok": false, "error": "FORBIDDEN_SCOPE", "message": "...", "request_id": "8f3c..."}
```

`request_id` viaja también en la cabecera `X-Request-Id` y es lo que se cita al
reportar un problema: aparece en el registro del servidor junto al error real.
Todas las respuestas son `no-store`: nunca se cachean en el dispositivo ni en
Cloudflare.

Códigos de error del contrato:

| Código | HTTP | Significado |
| --- | --- | --- |
| `MISSING_TOKEN` | 401 | Falta `Authorization: Bearer <token>` |
| `INVALID_TOKEN` | 401 | Token desconocido, revocado, expirado o de un usuario desactivado |
| `FORBIDDEN_SCOPE` | 403 | El token no tiene el alcance que el endpoint exige |
| `FORBIDDEN` | 403 | El alcance basta pero Odoo niega el permiso al usuario del token (grupo, compañía, regla de registro) |
| `NOT_FOUND` | 404 | El registro no existe |
| `VALIDATION_ERROR` | 400 | Datos o parámetros inválidos; `message` explica cuál |
| `INVALID_JSON` | 400 | El cuerpo no es un objeto JSON |
| `INVALID_CURSOR` | 400 | El cursor no fue emitido por este servidor |
| `RESYNC_REQUIRED` | 409 | El cursor es más viejo que el historial de bajas (90 días): sincronizar desde cero |
| `CONFLICT` | 409 | La operación choca con un registro existente; `details` lo describe |
| `RATE_LIMITED` | 429 | Más de 600 solicitudes por minuto con el mismo token |
| `INTERNAL_ERROR` | 500 | Fallo del servidor; el detalle queda en el registro, nunca en la respuesta |

Un error puede traer además `details` (objeto) con datos útiles para el
cliente, por ejemplo el producto existente en un `CONFLICT` de código de barras.

## 3. Autenticación

Cada caja o interfaz tiene **su propio token**, revocable por separado:

```
Authorization: Bearer <token>
```

Garantías, heredadas del emparejamiento del escáner que ya opera en staging:

- Se guarda **solo el hash** (SHA-256). Una copia de la base de datos no sirve
  para entrar.
- El texto plano se muestra **una vez**, al emitirlo. Si se pierde, se revoca y
  se emite otro.
- Un token **nunca puede hacer más que el usuario detrás de él**: la petición se
  ejecuta *como* ese usuario, así que las reglas de registro y los permisos de
  Odoo se aplican igual que en la pantalla; el alcance solo estrecha lo que ese
  usuario ya podía hacer. Los alcances, además, se limitan a su rol VLUX.
- Expiración opcional, revocación inmediata y `last_used_at` para detectar
  dispositivos olvidados.

### Alcances

| Alcance | Permite |
| --- | --- |
| `system:read` | Estado del sistema |
| `catalog:read` | Leer catálogo y precios |
| `catalog:write` | Alta y edición de productos |
| `orders:write` | Registrar ventas |
| `session:manage` | Abrir y cerrar caja |
| `dashboard:read` | Indicadores del negocio |

Qué puede otorgar cada rol (el resto se rechaza al emitir, no al usar):

| Rol VLUX | Alcances que puede otorgar |
| --- | --- |
| Owner, Administrador | todos |
| Supervisor | todos menos `catalog:write` |
| Cajero | `system:read`, `catalog:read`, `orders:write` |
| Operador de inventario | `system:read`, `catalog:read`, `catalog:write` |
| Auditor | `system:read`, `dashboard:read` |
| Soporte | `system:read` |

### Emitir un token

En Odoo: **Ajustes → API VLUX → Emitir token**. Se elige nombre, usuario,
alcances y expiración; el token aparece una sola vez para copiarlo a la caja.
**Ajustes → API VLUX → Tokens de la API** lista los vigentes y los revocados, y
desde el formulario se revoca.

Desde el shell, para automatizar el aprovisionamiento:

```python
token, raw = env["vlux.api.token"].issue("Caja 1", "catalog:read orders:write", user=env.ref("base.user_admin"))
print(raw)   # única vez
```

## 4. Endpoints

| Método | Ruta | Alcance | Para qué |
| --- | --- | --- | --- |
| GET | `/me` | `system:read` | Identidad del token, sus alcances y la tienda contra la que opera |
| GET | `/openapi.json` | público | El contrato completo, para generar un cliente |
| GET | `/store/config` | `system:read` | Compañía, datos fiscales, moneda, impuestos por defecto y cajas con sus métodos de pago |
| GET | `/catalog/products` | `catalog:read` | **Feed** de variantes vendibles con cursor y bajas |
| GET | `/catalog/customers` | `catalog:read` | **Feed** de clientes con cursor y bajas |
| GET | `/catalog/pos-categories` | `catalog:read` | Categorías del punto de venta (instantánea) |
| GET | `/catalog/categories` | `catalog:read` | Categorías internas de producto (instantánea) |
| GET | `/catalog/taxes` | `catalog:read` | Impuestos de venta de la compañía (instantánea) |
| GET | `/catalog/pricelists` | `catalog:read` | Listas de precios con sus reglas (instantánea) |
| POST | `/catalog/products` | `catalog:write` | Alta rápida de un producto vendible (mismas reglas que el diálogo del POS) |

`/me` es también la prueba de vida que debe ejecutar una caja al arrancar:
confirma token, tienda, moneda y hora del servidor.

```bash
curl -s https://tienda.vlux.com.mx/vlux/api/v1/me -H "Authorization: Bearer $VLUX_TOKEN"
```

La petición se ejecuta en la **compañía del token**: una caja solo ve los
productos, clientes, impuestos y cajas de su tienda, aunque el usuario tenga
acceso a varias compañías en Odoo.

### 4.1 Sincronización incremental (feeds)

Una caja VLUX guarda una copia local del catálogo y pide "lo que cambió desde
mi cursor". Los dos feeds (`/catalog/products`, `/catalog/customers`) comparten
el mismo protocolo:

```
GET /catalog/products?cursor=<opaco>&limit=500
→ {"items": [...], "deleted": [id, ...], "next_cursor": "...", "has_more": true, "server_time": "..."}
```

1. Sin `cursor` se empieza desde cero: se piden páginas hasta que `has_more`
   sea `false`, aplicando cada una y guardando **siempre** `next_cursor`.
2. A partir de ahí, cada llamada con el último cursor devuelve solo los
   registros cambiados después de él (`items`, con sus banderas) y los ids
   borrados de verdad en ese tramo (`deleted`). El cliente aplica los
   `items`, luego borra los `deleted`, luego guarda `next_cursor`.
3. Repetir una página con el mismo cursor devuelve lo mismo: un reintento
   nunca duplica ni pierde nada.

Reglas que hacen esto fiable:

- **Orden estable** por `(sello, id)`. En productos el sello es
  `vlux_sync_date`, que avanza cuando cambia la variante **o su plantilla**
  (nombre, precio, impuestos, categorías, imagen, archivado…); el
  `write_date` de Odoo no sirve porque renombrar una plantilla no toca sus
  variantes. En clientes el sello es `write_date`.
- **Bajas explícitas.** Archivar un producto o quitarlo del POS lo entrega con
  `active`/`available_in_pos` en `false`; solo un borrado real llega en
  `deleted`, gracias a una lápida (`vlux.catalog.tombstone`) escrita al
  borrar. Las lápidas se conservan 90 días; un cursor más viejo recibe
  `RESYNC_REQUIRED` y debe empezar de cero.
- **Ventana de asentamiento de 30 s.** Los sellos tienen precisión de un
  segundo y otra petición puede estar escribiendo en paralelo, así que una
  página nunca incluye lo escrito en los últimos 30 s: llega en la siguiente
  llamada. Una transacción que dure más de 30 s (una importación masiva en
  una sola transacción) podría quedar fuera; la importación estándar de Odoo
  confirma por lotes y no lo sufre.
- `limit` por defecto 500, máximo 1000. Cada página cuesta un número
  constante de consultas SQL (10) sea cual sea su posición.

Un producto del feed:

```json
{"id": 512, "template_id": 480, "name": "Playera (M)", "barcode": "7501234567890",
 "default_code": null, "list_price": 189.0, "currency_id": 33, "tax_ids": [4],
 "pos_category_ids": [2], "category_id": 1, "uom": {"id": 1, "name": "Unidades"},
 "type": "consu", "is_storable": true, "attributes": [{"attribute": "Talla", "value": "M"}],
 "active": true, "available_in_pos": true, "sale_ok": true, "company_id": 2,
 "sync_date": "2026-09-21T16:40:12.000000Z"}
```

`list_price` es el precio de lista de la variante en la moneda de la compañía
(sin impuestos ni lista de precios); las listas de precios se aplican encima con
`/catalog/pricelists`. Las existencias **no** forman parte del catálogo.

Medido en `tools/perf/bench_catalog_sync.py` con 10 000 productos: 21 páginas
de 500, p95 = 121 ms por página en proceso (283 ms extremo a extremo por HTTP
en el host de desarrollo), 10 consultas por página constantes, y la
sincronización incremental entrega exactamente los cambios y las bajas.

### 4.2 Alta rápida

`POST /catalog/products` con cuerpo JSON: `config_id` (caja) y `name`,
`barcode`, `list_price`; opcionales `default_code`, `is_storable`,
`initial_qty`, `pos_categ_id`, `categ_id`, `taxes_ids`, `image` (base64).
Aplica las mismas reglas que el diálogo del POS: el usuario del token necesita
el grupo de alta rápida y ser operador de POS, la caja debe ser de su compañía
y tener una sesión abierta (el stock inicial entra a la ubicación de esa
caja). Un código ya usado responde `CONFLICT` con `details.existing`.

Las fases C y D añaden venta, apertura y cierre de caja y métricas (ver
[PLAN_INICIATIVAS.md](PLAN_INICIATIVAS.md)).

## 5. Límites de uso

600 solicitudes por minuto y por token, con una ventana que se reinicia en una
sola sentencia SQL: dos procesos que atienden a la misma caja no pueden dejar
pasar una ventana doble. Es holgado para operar una caja y estrecho para que un
token filtrado sirva para vaciar el catálogo.

El contador vive en una transacción corta propia (`READ COMMITTED`), separada
de la de la petición. Sin eso, Odoo abre cada petición en `REPEATABLE READ` y
dos incrementos simultáneos del mismo contador terminan en
`could not serialize access due to concurrent update`: en una ráfaga de 620
peticiones con 20 en vuelo, 317 respondían 500 y el límite nunca se activaba.
Con la transacción propia, las 620 responden 200 o 429 y el límite se cumple
exacto (`tools/perf/bench_api_burst.py`). Dos consecuencias del diseño:

- **Una petición fallida también cuenta.** El contador se confirma aunque la
  petición termine en error, así que un cliente no puede gastar presupuesto
  gratis provocando errores.
- El registro de último uso del token (`last_used_at`) se actualiza igual, en
  su propia transacción y a lo sumo una vez por minuto, para que una ráfaga
  inicial sobre un token recién emitido no choque consigo misma.

Detrás de Cloudflare, el *Browser Integrity Check* rechaza con 403 (código
1010) algunos `User-Agent` genéricos, por ejemplo `Python-urllib`. Un cliente
de la API debe enviar un `User-Agent` propio (`vlux-pos-client/1.0`); `curl` y
los navegadores pasan sin más.

## 6. Qué se prueba en CI

`vlux_core/tests/test_api_v1.py` falla si el contrato cambia sin querer:
almacenamiento hasheado, alcances limitados por rol, token expirado, revocado o
de usuario desactivado, forma de `/me` y sus cabeceras, token nunca devuelto en
la respuesta, autenticación ausente o inválida, alcance insuficiente, límite de
uso, fuga de detalles en un error interno, y el propio OpenAPI.

`vlux_core/tests/test_api_catalog.py` cubre la fase B: el sello de
sincronización avanza con la plantilla y con la variante, las lápidas al borrar
variantes, plantillas y clientes, la sincronización completa por páginas sin
huecos ni duplicados, la incremental con renombrado, archivado, retiro del POS
y borrado, el reintento de página, cursores inválidos o caducos, alcance y
compañía, las instantáneas y `/store/config`.
`vlux_pos_catalog/tests/test_api_quick_create.py` cubre el `POST`: alta,
conflicto de código de barras, validación, alcance y permisos.
