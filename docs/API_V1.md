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
| `NO_OPEN_SESSION` | 409 | La caja no tiene una sesión abierta (venta o corte) |
| `CLOSING_REFUSED` | 409 | Odoo rechazó el corte (por ejemplo, diferencia arriba del límite sin encargado); la caja sigue abierta |
| `PAYMENT_MISMATCH` | 409 | Los pagos no cubren el total del servidor, o sobra dinero sin efectivo para dar cambio |
| `TOTAL_MISMATCH` | 409 | El `expected_total` de la caja no coincide al centavo con el total del servidor |

Toda petición corre dentro de un *savepoint*: si termina en error, **no deja
nada escrito** (una venta o un corte rechazados no dejan registros a medias).

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
| Cajero | `system:read`, `catalog:read`, `orders:write`, `session:manage` |
| Operador de inventario | `system:read`, `catalog:read`, `catalog:write` |
| Auditor | `system:read`, `dashboard:read` |
| Soporte | `system:read` |

### Token de una caja: atado y renovable

- **Atado a una caja** (`pos_config_id`): sólo sirve para esa caja. Abrir,
  cerrar, cotizar, vender, consultar ventas o dar de alta productos en otra
  caja responde `FORBIDDEN` ("Este token es de otra caja"), y `/store/config`
  sólo lista su caja. Un equipo perdido no sirve para operar otra caja. La
  PWA del POS rechaza tokens que no estén atados a una caja.
- **Renovable** (`lifetime_days`, 30 por defecto al emitirlo desde Odoo):
  caduca a los N días y el dispositivo lo renueva solo con
  `POST /token/rotate` antes de que eso pase (la PWA, cuando le quedan menos
  de 15 días). La respuesta trae el token nuevo **una sola vez**; el viejo
  sigue sirviendo 24 horas por si la respuesta se perdió, y pedirlo otra vez
  revoca el reemplazo que nunca se usó. Los alcances se vuelven a comprobar
  contra el rol actual del usuario: si perdió el rol, no se renueva.
- `/me` informa `token.register_id`, `token.expires_at` y `token.renewable`.

### Emitir un token

En Odoo: **Ajustes → API VLUX → Emitir token**. Se elige nombre, usuario,
caja (para el token de una caja), renovación, alcances y expiración; el token aparece una sola vez para copiarlo a la caja.
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
| POST | `/token/rotate` | cualquiera | Renovar el token del dispositivo (sólo si es renovable) |
| GET | `/store/config` | `system:read` | Compañía, datos fiscales, moneda, impuestos por defecto y cajas con sus métodos de pago |
| GET | `/catalog/products` | `catalog:read` | **Feed** de variantes vendibles con cursor y bajas |
| GET | `/catalog/customers` | `catalog:read` | **Feed** de clientes con cursor y bajas |
| GET | `/catalog/pos-categories` | `catalog:read` | Categorías del punto de venta (instantánea) |
| GET | `/catalog/categories` | `catalog:read` | Categorías internas de producto (instantánea) |
| GET | `/catalog/taxes` | `catalog:read` | Impuestos de venta de la compañía (instantánea) |
| GET | `/catalog/pricelists` | `catalog:read` | Listas de precios con sus reglas (instantánea) |
| POST | `/catalog/products` | `catalog:write` | Alta rápida de un producto vendible (mismas reglas que el diálogo del POS) |
| GET | `/catalog/products/<id>/image` | `catalog:read` | Miniatura del producto (binaria) con `ETag` y `304` |
| GET | `/registers/<id>/session` | `system:read` | Estado de la caja y su sesión actual |
| GET | `/registers/<id>/employees` | `orders:write` | Empleados que pueden usar la caja, con rol y PIN cifrado |
| POST | `/registers/<id>/session/open` | `session:manage` | Abrir la caja con el efectivo inicial |
| GET | `/registers/<id>/session/closing` | `session:manage` | Lo esperado en el corte |
| POST | `/registers/<id>/session/close` | `session:manage` | Corte de caja con lo contado |
| POST | `/orders/quote` | `orders:write` | Precios, impuestos y total de un carrito (no registra nada) |
| POST | `/orders` | `orders:write` | Registrar una venta pagada, idempotente por `uuid` |
| GET | `/orders/<uuid>` | `orders:write` | Consultar una venta por su `uuid` |
| GET | `/credit/customers` | `catalog:read` | Clientes con crédito autorizado o que deben: límite, saldo, disponible |
| GET | `/credit/customers/<id>` | `catalog:read` | Crédito de un cliente, al momento |
| POST | `/credit/customers/<id>` | `orders:write` | Autorizar o retirar crédito y fijar límite (sólo encargado o dueño) |
| POST | `/credit/abonos` | `orders:write` | Abono de un cliente, idempotente por `uuid` |
| POST | `/customers` | `orders:write` | Alta de cliente desde la caja (permiso de Odoo *Creación de contactos*) |

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
   Durante esa sincronización inicial `deleted` viene vacío (el cursor lleva
   una marca interna de "inicial"): quien no tiene nada no necesita olvidar
   nada. Al completarse, el cursor pasa a ser uno normal.
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

`image_version` es la versión de la foto (el checksum del adjunto de Odoo) o
`null` si no tiene; cambia exactamente cuando cambia la imagen y **nunca viaja
base64 en el feed**. La imagen se descarga aparte:

```
GET /catalog/products/<id>/image?size=256        → 200 image/png|jpeg, ETag: "<image_version>-256"
GET ... con If-None-Match: "<image_version>-256" → 304 sin cuerpo
```

Tamaños: 128, 256 (por defecto, la baldosa del POS), 512 y 1024: las
miniaturas que Odoo ya tiene precalculadas en su filestore; nunca la original.
`Cache-Control: private, max-age=86400, must-revalidate`: el dispositivo puede
guardarla un día y revalidar con el `ETag`; Cloudflare y otras cachés
compartidas no la guardan. Errores: `NO_IMAGE` (404) si el producto no tiene
foto, `NOT_FOUND` (404) si no existe o es de otra compañía (no se distingue a
propósito), `VALIDATION_ERROR` si el tamaño no es uno de los cuatro. Solo se
enruta un id entero: no hay rutas ni nombres de archivo arbitrarios.

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

### 4.3 Operación de venta (fase C)

Módulo `vlux_pos_api`. Todo pasa por los mismos métodos de Odoo que usa su POS
(`pos.session.set_opening_control`, `post_closing_cash_details`,
`close_session_from_ui`, `pos.order.sync_from_ui`): sesiones, cortes,
inventario y contabilidad son idénticos se venda en la pantalla que se venda.
Las ventas registradas por la API llevan `source = vlux_api`.

**Empleados.** Si la caja usa inicio de sesión por empleado (`employee_login`
en `/registers/<id>/session`, las cajas VLUX lo usan), la apertura, el corte y
cada venta llevan `employee_id`, que debe ser un empleado permitido en la caja.
`/registers/<id>/employees` entrega `role` (`manager`, `cashier`, `minimal`)
y `pin_sha1`/`barcode_sha1` para validar el PIN sin red, igual que el POS de
Odoo. **Riesgo aceptado:** un PIN de 4 dígitos en SHA-1 sin sal se descifra por
fuerza bruta; por eso sólo se entrega a un token que ya puede vender en esa
caja (`orders:write`), el mismo nivel de confianza que el POS de Odoo.

**PIN verificado por el servidor.** `POST /registers/<id>/employees/login`
`{"employee_id", "pin"}` compara el PIN en el servidor (comparación de tiempo
constante) y responde una **sesión de empleado** (`session`, válida un turno de
12 h, sólo en esa caja y con el token de ese equipo) que viaja en la cabecera
`X-Vlux-Employee`. Cinco PIN incorrectos bloquean a ese empleado en esa caja 15
minutos (`PIN_LOCKED`); un encargado sin PIN no puede entrar.
`POST /registers/<id>/employees/logout` la termina.

- **Requieren la sesión** (`PIN_REQUIRED` si falta o venció): abrir y cerrar
  caja, autorizar crédito y registrar abonos.
- **Ventas:** una venta lleva `employee_session` (la sesión con la que se hizo;
  así una venta en cola se valida aunque se envíe después). Si la caja validó
  el PIN sin internet no hay sesión: la venta **se registra igual** (nunca se
  pierde) y queda `vlux_api_employee_verified = False`; las reglas que dependen
  de quién vende (crédito) la tratan como venta de cajera y la marcan.
- `pin_sha1` de `/registers/<id>/employees` sigue existiendo sólo para validar
  sin internet.

**Apertura.** `POST /registers/<id>/session/open`
`{"opening_cash": 500, "employee_id": 7, "notes": "..."}`. Repetirla es
inofensivo: con la sesión abierta responde `already_open: true` sin cambiar
nada. Si la caja está en corte responde `CONFLICT`.

**Cotización.** `POST /orders/quote`
`{"register_id": 1, "lines": [{"product_id": 512, "qty": 2}], "partner_id": null}`
devuelve cada línea con precio, impuestos y subtotales, y `amount_untaxed`,
`amount_tax` y `amount_total` calculados con el mismo motor que
`pos.order._compute_prices` (redondeo de la compañía y de la caja). Sin
`price_unit` decide la lista de precios de la caja (o la del cliente si la caja
la ofrece); con `price_unit`, la línea se cobra a ese precio y
`price_overridden` dice si difiere del catálogo.

**Venta.** `POST /orders`:

```json
{"uuid": "5f0c…", "register_id": 1, "employee_id": 7,
 "lines": [{"product_id": 512, "qty": 2, "price_unit": 20.0}],
 "payments": [{"payment_method_id": 1, "amount": 100.0}],
 "expected_total": 40.0, "created_at": "2026-09-28T18:04:11Z",
 "partner_id": null, "session_id": 31}
```

- **Idempotente por `uuid`** (lo genera la caja). Reenviar la misma venta
  devuelve la ya registrada con `duplicate: true`; nunca crea otra. Si dos
  envíos llegan a la vez, uno responde `CONFLICT` con `details.retry` y el
  reintento encuentra la venta.
- **Totales del servidor.** El total que se contabiliza es el calculado aquí;
  `expected_total` (recomendado) se compara al centavo y, si no coincide,
  `TOTAL_MISMATCH` con el desglose del servidor en `details`.
- **Pagos.** Deben cubrir el total (con el redondeo de efectivo de la caja, si
  lo tiene). El cambio sólo sale de efectivo: con tarjeta de más responde
  `PAYMENT_MISMATCH`. La forma de pago Crédito se rechaza por ahora: las ventas
  a crédito y los abonos siguen en el POS de Odoo.
- **Precio distinto al catálogo:** se acepta (una caja sin red cobra con su
  copia local) y la orden queda marcada `vlux_api_price_overridden` para el
  dueño.
- **Sesión.** La venta entra en la sesión abierta de la caja; una venta hecha
  antes de un corte que llega después entra en la sesión abierta en ese
  momento, como en Odoo. Sin sesión abierta: `NO_OPEN_SESSION`.
- `created_at` es la hora de la venta en la caja (UTC); una fecha futura se
  ignora.

Cualquier `409` significa que **no se escribió nada**: la caja conserva la
venta en su cola y muestra el motivo. La respuesta de éxito (y
`GET /orders/<uuid>`) trae `name`, `pos_reference`, totales, `change`, líneas
y pagos para imprimir el ticket.

**Corte.** `GET /registers/<id>/session/closing` da lo esperado: efectivo
(apertura, ventas, entradas y salidas, esperado) y cada otra forma de pago.
`POST /registers/<id>/session/close`
`{"session_id": 31, "employee_id": 7, "counted_cash": 1250.0, "counted": [{"payment_method_id": 2, "amount": 830.0}], "notes": "..."}`.
`session_id` debe ser la sesión abierta (una pantalla vieja no puede cerrar
otra sesión). La regla de diferencia máxima (D6, $30) la aplica el servidor: por
encima del límite sólo cierra un encargado (el empleado que cuenta); si no,
`CLOSING_REFUSED` y la caja **sigue abierta**.

### 4.4 Crédito (fiado)

Módulo `vlux_pos_credit`, con las mismas reglas que en el POS de Odoo (D7) y
sus mismos métodos:

- **Saldos.** `GET /credit/customers` lista a los clientes con crédito
  autorizado o con saldo: `allowed`, `limit` (0 = sin límite), `balance`,
  `available` (`null` sin límite) y `over_limit`. El saldo sale de los pagos
  a cuenta de cliente del POS (ventas a crédito suman, abonos y devoluciones
  restan), igual que en la caja de Odoo y el panel del dueño.
- **Autorizar.** `POST /credit/customers/<id>`
  `{"register_id", "employee_id", "allowed": true, "limit": 1000}`: sólo el
  encargado o el dueño (si no, `FORBIDDEN`). Queda registrado en el cliente.
- **Vender a crédito.** `POST /orders` con un pago de la forma de pago
  *Crédito* (tipo `pay_later`) y `partner_id` (obligatorio). El servidor **no
  rechaza** una venta a crédito que rompe una regla (la vendió una cajera, el
  cliente no está autorizado o rebasa su límite): la guarda y la **marca** para
  el dueño, porque la mercancía ya salió. La respuesta trae `credit` con
  `amount`, `previous_balance`, `new_balance`, `flagged` e `issues` para el
  ticket "VENTA A CRÉDITO".
- **Abonos.** `POST /credit/abonos`
  `{"uuid", "register_id", "partner_id", "amount", "payment_method_id", "employee_id"}`:
  cualquier cajera, en efectivo o tarjeta, nunca más de lo que debe; entra a
  la sesión abierta, así que el efectivo cuenta en el corte. Reenviar el
  mismo `uuid` devuelve el mismo abono. Sin caja abierta: `NO_OPEN_SESSION`.
- **Clientes nuevos.** `POST /customers` `{"name", "phone"?, "email"?, "vat"?}`
  se crea como el usuario del token: decide el permiso de Odoo *Creación de
  contactos*, igual que en el POS de Odoo.

La fase D añade métricas y diagnóstico de la API (ver
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
conflicto de código de barras, validación, alcance y permisos. La imagen:
miniatura de 256 px por defecto, `Content-Type`, `ETag` igual a
`image_version`, `304`, ETag distinto por tamaño y por foto nueva, `NO_IMAGE`,
tamaño inválido, id inexistente, id no numérico, alcance, sin token y producto
de otra compañía. La sincronización inicial sin `deleted`.

`vlux_pos_api/tests/test_api_sales.py` cubre la fase C por HTTP: estado de la
caja, empleados y PIN sólo con `orders:write`, apertura que exige empleado y
es repetible, cotización con IVA incluido, venta enviada dos veces que queda
una sola y cuyo total coincide con el que recalcula Odoo, ventas rechazadas
(pago corto, tarjeta de más, total distinto) sin dejar nada escrito, cambio
de un pago mixto, precio distinto al catálogo marcado, venta sin sesión, corte
de una cajera rechazado por diferencia con la caja todavía abierta, corte
cuadrado y corte de un encargado por encima del límite.
