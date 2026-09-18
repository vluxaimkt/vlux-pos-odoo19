# VLUX POS — Operabilidad y rendimiento

Documentación técnica de la iniciativa *VLUX POS Operability & Performance*
(rama `feature/pos-operability-performance`, base `506d671`). Describe lo que
existe en código: arquitectura, contratos de API, decisiones técnicas, cómo
medir y cómo probar. La gestión del proyecto vive fuera de Git.

## Estado por fase

| Fase | Contenido | Estado |
| --- | --- | --- |
| 0 | Auditoría del repositorio | Completa |
| 1 | Baseline de rendimiento reproducible (`tools/perf`) | Completa |
| 2 | `vlux_pos_catalog`: alta rápida de productos desde el POS | Completa |
| 3 | Scanner V2: push/batch de resultados, cola, idempotencia, cámara | Completa |
| 4 | Owner V2: agregaciones en base de datos | Completa |
| 5 | POS performance: apertura, catálogo, compresión | Completa |
| 6 | Observabilidad (`/vlux/ready`, doctor) | Completa |
| 7 | Scale hardening (perfiles de capacidad, PostgreSQL) | Pendiente |
| 8 | CI dividida (rápida / E2E / performance) y regresión completa | Pendiente |

`PRODUCTION_GO=NOT_YET`: nada de esta rama está validado manualmente ni
integrado en `main`.

## Hallazgos de la auditoría (fase 0)

Sin hallazgos críticos de pérdida de datos ni secretos versionados. Los puntos
altos, y su estado:

| Hallazgo | Estado |
| --- | --- |
| Polling de resultados cada 250 ms por scan y scans serializados en el teléfono | Resuelto (fase 3) |
| `authenticate_mobile_token` escribía `last_seen_at` en cada request | Resuelto (fase 3) |
| Cooldown servidor de 1.5 s bloqueaba repeticiones intencionales; `request_id` generado por el servidor (reintentos duplicaban) | Resuelto (fase 3) |
| Odoo 19 ignora `?db=`; el cliente móvil dependía de monodb/dbfilter | Mitigado: el cliente envía `X-Odoo-Database` en HTTP; push requiere monodb/dbfilter (como producción) |
| Sin alta rápida de productos | Resuelto (fase 2) |
| ZXing decodificaba el frame completo cada 120 ms en un canvas nuevo | Resuelto (fase 3) |
| Owner: órdenes y líneas del día iteradas en Python, 500 productos con `qty_available`, refresco 30 s | Resuelto (fase 4) |
| `/vlux/health` sólo liveness; sin readiness ni doctor | Resuelto (fase 6) |
| Prefijo R2 puede producir `tenants/tenants/...`; capacidad fija (`workers=2`) | Pendiente (fase 7) |

## vlux_pos_catalog

Addon independiente (`depends`: `point_of_sale`, `stock`, `vlux_core`). Cuando
el POS recibe un código de barras sin producto, un usuario autorizado ve el
formulario rápido en lugar del aviso genérico *Unknown Barcode*.

### Flujo

```
barcodeReader.scan() -> ProductScreen._barcodeProductAction (patch)
    producto encontrado  -> flujo estándar de Odoo
    no encontrado + permiso -> QuickProductDialog (no bloquea el mutex de escaneo)
        Guardar -> product.template.vlux_pos_quick_create(values, config_id)
               -> pos.loadNewProducts([["id", "=", tmpl_id]])   (API nativa Odoo 19)
               -> addLineToCurrentOrder(...)                      (sin recargar el POS)
```

### Autorización (server-side)

Grupo `vlux_pos_catalog.group_vlux_catalog_quick_create` ("VLUX Catalog: alta
rápida en POS"):

- implicado por `VLUX Administrator` (y por tanto `VLUX Owner`) y por
  `VLUX Inventory Operator`;
- `VLUX Cashier` **no** lo recibe; se asigna explícitamente por usuario;
- el método exige además `point_of_sale.group_pos_user` y una sesión abierta en
  una `pos.config` visible para el usuario (reglas multi-compañía normales).

El grupo no otorga `product.group_product_manager`: un cajero con alta rápida
sigue sin poder crear productos por el backend. La creación usa `sudo()` sólo
para el `create` final con valores validados y whitelisted.

### API RPC (`product.template`)

| Método | Descripción |
| --- | --- |
| `vlux_pos_quick_create_defaults(config_id)` | Impuestos por defecto de la compañía, `is_storable` por defecto (`ir.config_parameter` `vlux_pos_catalog.default_is_storable`, `1` por defecto), categoría POS sugerida |
| `vlux_pos_quick_create(values, config_id)` | Crea el producto. Devuelve `{"ok": true, product_tmpl_id, product_id, barcode}` o `{"ok": false, "code": "BARCODE_EXISTS", "existing": {...}}` |
| `vlux_pos_enable_existing(product_id, config_id)` | Reactiva/marca `available_in_pos` un producto existente o archivado |
| `vlux_audit_duplicate_barcodes(company)` | Auditoría de códigos duplicados (un `GROUP BY`), para diagnóstico y preflight |

`values` aceptados: `barcode` (3–64 alfanuméricos), `name`, `list_price`,
`default_code`, `pos_categ_id`, `categ_id`, `taxes_ids` (sólo impuestos de
venta de la compañía), `is_storable`, `initial_qty`, `image` (base64).

### Unicidad de códigos

Odoo 19 valida la unicidad por compañía con una restricción Python, sin índice
UNIQUE. No se añadió un índice global (rompería bases con duplicados legítimos
entre compañías). En su lugar: `pg_advisory_xact_lock` por `(company, barcode)`
durante el alta, búsqueda previa (incluye archivados y empaques `product.uom`)
y la auditoría `vlux_audit_duplicate_barcodes`.

### Imágenes

La foto se elige manualmente (`<input type="file" accept="image/*"
capture="environment">`, abierto sólo por acción del usuario). El cliente la
reduce a ≤1024 px JPEG 0.85 (`image_utils.js`) antes de enviarla; el servidor
valida que sea imagen y la guarda en `image_1920`, de donde Odoo deriva
`image_128` para las tarjetas del POS. Todo queda en `ir.attachment`/filestore
estándar; no hay almacenamiento paralelo.

### Stock inicial

Si `is_storable` y `initial_qty > 0`, se aplica un ajuste de inventario
(`stock.quant` en `inventory_mode` + `action_apply_inventory`) en la ubicación
de origen del tipo de operación de la caja (o el stock del almacén de la
compañía), con historial de movimientos.

## Scanner V2 (`vlux_mobile_scanner` 19.0.2.0.0)

Se conservan tokens hasheados, pairing temporal, `request_id` único, rate limit
persistente, sesión POS, bus nativo `pos.config._notify`, ACK, revocación y
expiración. Cambia la entrega de resultados y el manejo de repeticiones.

### Protocolo

```
Teléfono                            Odoo                              POS
POST /vlux/mobile/scan {barcode, request_id (UUID v4 cliente)}
                              -> vlux.mobile.scanner.event (queued)
                              -> pos.config._notify(VLUX_MOBILE_BARCODE)  -> barcodeReader.scan()
                                                                          <- POST /vlux/pos/ack
                              <- event.apply_pos_result()
   <- bus push VLUX_MOBILE_RESULT en canal privado (websocket /websocket)
   (fallback) POST /vlux/mobile/results {request_ids: [...]}  (≤50 ids por llamada)
```

| Endpoint | Cambio |
| --- | --- |
| `POST /vlux/mobile/pair`, `/heartbeat` | Devuelven además `push_channel`, `push_version` (versión del worker websocket de Odoo) y `results_batch_max` |
| `POST /vlux/mobile/scan` | Acepta `request_id` del cliente. Misma id → mismo evento, respuesta `200 {duplicate: true}` sin segunda notificación al POS. Id ajena a otro pairing → `409 REQUEST_ID_CONFLICT`. Ids no UUID se ignoran y se genera una en servidor |
| `POST /vlux/mobile/results` | Nuevo. Lookup batch de varios `request_id` en una consulta; ids ajenas → `status: "unknown"` |
| `POST /vlux/mobile/result` | Se mantiene por compatibilidad |
| `POST /vlux/pos/ack` | Pasa por `event.apply_pos_result()`, que guarda y empuja el resultado al teléfono |

Rate limits por pairing (por minuto): scan 240, results 120, heartbeat 12.

### Canal push

`push_channel = "vlux_mobile_scanner:" + sha256("push:" + token_hash)[:40]`.
Se deriva del hash del token (nunca del token), se calcula en servidor y sólo se
entrega al teléfono que ya posee el token. El teléfono abre
`/websocket?version=<push_version>` y envía
`{"event_name": "subscribe", "data": {"channels": [push_channel], "last": 0}}`,
el mismo contrato que usa el cliente web de Odoo. Los websockets son públicos
en Odoo (usuario `public`); el nombre de canal actúa como capacidad
no adivinable, igual que los canales de livechat.

Requisito: la sesión pública del websocket debe persistirse, por lo que el
push funciona en hosts monodb o con `dbfilter` (toda instalación productiva
VLUX). Sin eso el cliente cae automáticamente al polling batch.

### Cliente móvil

- `scanner_core.js` (lógica pura, sin DOM, probada con `node --test`):
  - `ScanGate`: un código se acepta si es distinto al último, o si **salió del
    encuadre** ≥400 ms y pasaron ≥700 ms desde la última aceptación. Un código
    quieto ante la cámara se acepta exactamente una vez; retirarlo y volver a
    enfocarlo cuenta como otro scan. Entradas manuales y el botón *+1 Repetir*
    saltan la compuerta (intención explícita).
  - `PendingQueue`: cola local ordenada con reintentos (máx. 4, backoff 400 ms →
    4 s) y expiración (30 s).
  - `PollBackoff`: 300 ms → ×1.6 → 1500 ms; se reinicia al recibir resultados y
    se detiene con la cola vacía. Con push activo sólo se consultan scans
    pendientes >3 s (red de seguridad cada 2 s).
- `scanner.js`: scans en paralelo (sin `state.sending`), cabecera
  `X-Odoo-Database`, heartbeat cada 30 s, reconexión del websocket con
  backoff, reintento al volver `online`/visible, historial de últimos
  resultados y badge "N en proceso".
- Cámara: `BarcodeDetector` nativo primero; ZXing sólo como fallback y sólo
  sobre el marco de escaneo (80 % × 40 % centrado) reducido a ≤640 px en un
  canvas reutilizado; `getUserMedia` pide 1280×720; el bucle se ralentiza con
  la pestaña oculta. No se usa Web Worker: el detector nativo ya corre fuera
  del hilo principal y el recorte+downscale deja a ZXing dentro de presupuesto.

### Base de datos

`authenticate_mobile_token` es de sólo lectura en el camino normal:
`last_seen_at` se escribe como máximo una vez por minuto y siempre en el
heartbeat. `cooldown_ms` (anti-rebote en servidor) queda en 0 por defecto y se
mantiene como opción. El rate limit por upsert en PostgreSQL se conserva: con
1 request por scan su coste es una fila por pairing.

## Owner V2 (`vlux_owner` 19.0.1.3.0)

`vlux.owner.dashboard.service.get_dashboard` conserva exactamente la forma del
JSON (`/vlux_owner/api/dashboard` y `/vlux_owner/api/share/dashboard`), pero
todas las métricas se agregan en PostgreSQL. El número de queries es fijo
(20 en las mediciones) sin importar cuántas órdenes tenga el día.

| Bloque | Cálculo |
| --- | --- |
| Ventas, tickets, tendencia | Un `_read_group` sobre `pos.order` por `date_order:hour_number` con `tz` del usuario en contexto: Odoo 19 aplica `timezone(tz, timezone('UTC', date_order))` en SQL, así que la hora es local |
| Comparación | `amount_total:sum` de ayer hasta la misma hora local (hoy) o del día anterior completo (fechas pasadas) |
| Unidades y top 10 | `_read_group` sobre `pos.order.line` con `order_id any <dominio del día>`, orden `qty desc, importe desc, producto` |
| Ventas por caja | `_read_group` por `config_id` (stored en Odoo 19); estado `open` si la caja tiene hoy una sesión en `opening_control`/`opened`/`closing_control` |
| Últimas ventas | `search_fetch` con `limit=10` y sólo los campos necesarios |
| Stock bajo | `stock.quant._read_group` por producto (ubicaciones internas de la compañía, `having quantity <= umbral`) más una búsqueda acotada de productos sin ningún quant (existencia 0). Coincide con `qty_available` |

Correcciones de comportamiento incluidas:

- **Tendencia por hora**: las etiquetas son bloques de 2 h (`00`, `02`, …) y
  antes sólo se leía la hora par; las ventas en horas impares no aparecían.
  Ahora cada bloque suma sus dos horas.
- **Stock bajo multi-company**: la búsqueda con `sudo()` ignoraba las reglas
  de compañía y mostraba productos de otras empresas; ahora se filtra
  `company_id in (False, compañía)`. El `limit=500` sin orden (resultado
  arbitrario con catálogos grandes) desaparece.
- **Estado de caja**: se toma de la sesión actual de la caja, no de la sesión
  de su última venta.

### Caché y refresco

- Servidor: caché en memoria por proceso con TTL de 15 s por (base de datos,
  compañía, zona horaria, día). Parámetro `vlux_owner.dashboard_cache_ttl`
  (segundos, 0 = desactivada, máximo 300). Sólo se cachean métricas; nombre
  del usuario, compañía y moneda se calculan en cada llamada. Cada worker
  tiene su propia caché: en el peor caso un dueño ve datos de hace 15 s.
  `ormcache` no sirve aquí porque no expira por tiempo.
- Cliente (`static/dist/assets/app.js`): refresco cada 30 s sólo con la app
  visible (`document.hidden`), actualización inmediata al volver a primer
  plano si los datos tienen más de 10 s, y sin peticiones solapadas.

## POS: apertura y catálogo (fase 5)

Siguiendo [la decisión de arquitectura](ARQUITECTURA_HEADLESS.md), la fase se
centró en la capa de datos y en mejoras de bajo coste; no se invirtió en la
interfaz OWL del POS.

### Cómo carga Odoo 19 el POS

- **Primera apertura** (o tras cambiar la configuración de la caja):
  `pos.session.load_data` envía catálogo, impuestos, métodos de pago, etc.
  Los productos se limitan a `point_of_sale.limited_product_count` (5 000 por
  defecto), priorizando favoritos, los de movimiento de stock reciente y los
  modificados recientemente.
- **Reaperturas**: el navegador guarda los datos en IndexedDB y sólo pide los
  registros con `write_date` posterior a su última sincronización
  (`pos_last_server_date`) más la lista de registros a retirar
  (`filter_local_data`).
- **Productos no cargados**: un código escaneado o una búsqueda que no está en
  memoria se resuelve en el servidor (`load_product_from_pos`).

### Dónde se va el tiempo y el tamaño (10k productos, 5 002 cargados)

`tools/perf/pos_payload_breakdown.py`: el 97 % de los 6.06 MB son
`product.template` (67 %) y `product.product` (30 %), repartidos en ~35 campos
por fila sin ningún campo dominante. Los addons VLUX añaden ~100 bytes
(`type_tax_use` en impuestos y un booleano de usuario).

El perfil del servidor mostró que ~⅓ del tiempo de `load_data` era
`_add_archived_combinations`: una llamada a `_get_attribute_exclusions` por
cada plantilla, aunque no tenga atributos. Una plantilla sin
`product.template.attribute.value` no puede tener exclusiones ni combinaciones
archivadas, así que `vlux_pos_catalog` (`models/pos_load.py`) sólo calcula las
plantillas con atributos y devuelve `[]` para el resto. El payload es idéntico
byte a byte; `tests/test_pos_load.py` lo compara con la implementación estándar.

### Compresión

El payload JSON comprime al 6.5 % (6.06 MB → 393 KB con gzip). El Caddy de la
nube ya usaba `encode zstd gzip`; el Caddyfile que genera la instalación
Windows (`packaging/windows/service-host/Program.cs`) no comprimía y ahora sí,
lo que importa en tablets y teléfonos por Wi-Fi.

### Mediciones (Windows Server, PostgreSQL 16 local)

| Escenario (10k productos) | Antes | Después |
| --- | --- | --- |
| Primera apertura, servidor (`load_data` p50) | 3 765 ms | **2 540 ms** (−33 %) |
| Primera apertura, bytes por la red (Caddy) | 6.06 MB (Windows sin compresión) | 393 KB |
| Reapertura con caché (`--incremental`) | 108 ms, 6 KB | 101 ms, 6 KB |
| Código no cargado → servidor (`load_product_from_pos`) | — | 36 ms p50 |
| Búsqueda en servidor (30 resultados) | — | 67 ms p50 |
| Arranque → `/vlux/health` | 7.0 s, 151 MB | 5.6 s, 150 MB |

"Antes" de la primera apertura es el código actual sin la optimización (misma
base clonada); la base de referencia `506d671` da 3 689 ms, es decir, los addons
VLUX no introducen regresión. Límite de productos precargados y coste de la
primera apertura en servidor: 1 000 → 0.7 s (1.37 MB), 2 000 → 1.1 s (2.54 MB),
5 000 → 2.5 s (6.06 MB). Se mantiene 5 000: menos productos precargados
significa menos catálogo disponible sin conexión, y cada producto no precargado
cuesta un viaje al servidor al escanearlo. Es un parámetro por instalación.

### Descartado en esta fase

- Medir el render del POS en navegador: es interfaz de Odoo que se sustituirá;
  los 12 tours cubren la ausencia de regresiones funcionales.
- Un servicio propio de sincronización de catálogo: Odoo ya lo resuelve para
  su POS. El de la API v1 se diseñará con su autenticación (cursor
  `(write_date, id)`, bajas explícitas y páginas acotadas; ver
  [ARQUITECTURA_HEADLESS.md](ARQUITECTURA_HEADLESS.md)).

### Tour de push estable

`VluxScannerPushDeliveryTour` fallaba de forma intermitente (2 de 4 corridas):
el teléfono simulado hacía polling a los 300 ms y, con el ACK del POS en
~320 ms, a veces el polling llegaba antes que el push. No había pérdida de
datos. El tour ahora usa el mismo retardo que el cliente real
(`PUSH_SAFETY_NET_MS` = 3 s): 5 de 5 corridas en verde.

## Observabilidad (fase 6)

### `/vlux/health` y `/vlux/ready`

| Ruta | Pregunta que responde | Quién la usa |
| --- | --- | --- |
| `/vlux/health` | ¿El proceso Odoo responde? (liveness, sin cambios: `{"status": "ok"}`) | Healthcheck de Docker, Caddy, service host de Windows, `vlux-cloud health` |
| `/vlux/ready` | ¿Esta instancia puede vender ahora? (readiness) | Monitoreo, balanceadores, `vlux-cloud doctor` |

`/vlux/ready` es pública, de sólo lectura y no crea sesión. Responde 200 con
`"status": "ready"` o 503 con `"not_ready"`, y un objeto `checks`:

| Check | `ok` cuando | Otros valores |
| --- | --- | --- |
| `database` | `SELECT 1` responde | `error` |
| `addons` | Están instalados los addons de la edición (`vlux_core.edition`) | `missing` |
| `module_updates` | Ningún módulo en `to install` / `to upgrade` / `to remove` | `pending` |
| `pos_config` | Existe al menos una caja activa | `missing` |

Sólo devuelve esos códigos: ni nombres de módulos, ni base de datos, ni rutas
(lo verifica el test). El detalle está en `/vlux/system/info` (rol VLUX
Support), que ahora incluye `ready`. No sustituye al healthcheck de Docker a
propósito: un contenedor "no listo" (p. ej. durante una actualización de
módulos) no debe reiniciarse.

### `vlux-cloud doctor <tenant>`

Diagnóstico de sólo lectura de un tenant, ejecutado en el host (nunca expuesto
por HTTP). Imprime un único JSON y termina con 0 (OK), 1 (WARN) o 2 (FAIL):

| Sección | Qué mira | WARN / FAIL |
| --- | --- | --- |
| ODOO | Contenedor, `/vlux/health`, `/vlux/ready` | FAIL si no responde o 503; WARN si no existe `/vlux/ready` (addon sin actualizar) |
| POSTGRES | `pg_isready`, versión, tamaño, transacción más larga | FAIL si no responde; WARN con transacciones de más de 10 min |
| DB_CONNECTIONS | `pg_stat_activity` frente a `max_connections` | WARN al 80 % |
| FILESTORE | Archivos y bytes | — |
| DISK | Espacio libre del volumen del tenant | WARN < 15 %, FAIL < 5 % |
| EDGE | Ruta de Caddy y contenedor, o estado del túnel | FAIL si falta la ruta o el túnel no está conectado |
| BACKUP_AGE | Último respaldo local (misma regla que `status`, 48 h) | WARN; FAIL con `REAL_CLIENT_DATA` |
| OFFSITE | Copia externa configurada y exitosa | WARN |
| WORKERS | Workers frente a 2 × CPU + 1 del host | WARN si se excede |
| ADDONS | Addons productivos instalados y sin actualizaciones pendientes | FAIL |
| VERSION | Versión del CLI y del tenant, imagen, commit de Odoo | — |

La recolección (Docker, `psql`, `curl`) y la evaluación están separadas:
`doctor_evaluate` es una función pura que `scripts/release/cloud_checks.py`
(`DOCTOR_CONTRACT`) prueba con escenarios sintéticos, incluida la ausencia de
credenciales en la salida. La recolección no se ha ejecutado aún contra un
tenant real: queda para la validación en el host de staging.

## Herramientas de rendimiento (`tools/perf`)

Ver [`tools/perf/README.md`](../tools/perf/README.md). Datos siempre
sintéticos; los generadores rechazan bases cuyo nombre contenga `prod` o
`real`.

| Herramienta | Mide |
| --- | --- |
| `seed_synthetic.py` | Catálogo (1k/10k/50k) y ventas (10k/100k) sintéticos, idempotente |
| `bench_owner.py` | Latencia p50/p95, queries y registros ORM de `get_dashboard` |
| `bench_pos_load.py` | `pos.session.load_data`: latencia, queries, bytes |
| `bench_scanner.py` | Requests por scan, latencia scan→resultado, perdidos/duplicados (`v1`, `batch`, `push`) |
| `bench_startup.py` | Arranque hasta `/vlux/health`, RSS, conexiones PG |
| `compare.py` | Tabla BEFORE/AFTER y budgets relativos/absolutos |

### Mediciones (Windows Server, PostgreSQL 16 local, sin proxy)

Baseline (BEFORE, `506d671`):

| Métrica | 1k prod / 1k órdenes | 10k prod / 10k órdenes |
| --- | --- | --- |
| Owner `get_dashboard` p50 / p95 | 217 / 341 ms | 738 / 817 ms |
| Owner queries por llamada | 27 | 46 |
| Owner valores ORM en caché | 59 577 | 409 321 |
| POS `load_data` p50 (payload) | 2.0 s (1.36 MB) | 3.7 s (6.06 MB, 5 002 templates) |
| Arranque → `/vlux/health` | 7.0 s, RSS 151 MB, 8 conexiones PG | — |

Owner V2 (AFTER). El BEFORE de arriba se tomó con otro "día de hoy" (el
dataset sesga las órdenes hacia la fecha de siembra), así que para comparar
se volvió a medir la implementación anterior sobre el mismo estado de la
base. Ambas devuelven los mismos totales (ventas, tickets, unidades, ticket
promedio). `bench_owner.py --cache cold` vacía la caché antes de cada llamada:

| Métrica | 1k (58 órdenes hoy) antes → después | 10k (614 órdenes hoy) antes → después |
| --- | --- | --- |
| `get_dashboard` p50 | 194 → 62 ms | 564 → 136 ms |
| `get_dashboard` p95 | 206 → 81 ms (0.39×) | 604 → 162 ms (0.27×) |
| Queries por llamada | 27 → 20 | 36 → 20 |
| Valores ORM en caché | 46 292 → 2 124 | 236 666 → 2 124 (0.01×) |
| Con caché caliente (`--cache warm`) | — | p50 12.8 ms |

Scanner (POS simulado con ACK a 150 ms / 600 ms):

| Escenario | v1 (BEFORE) | batch | push (AFTER) |
| --- | --- | --- | --- |
| Requests HTTP por scan, POS rápido | 2.0 | 1.7 | 1.0 |
| Requests HTTP por scan, POS lento | 3.27 | 1.63 | 1.0 |
| Throughput | 1.2–2.5 scans/s | 3.6–4.1 | 3.8–5.1 scans/s |
| Mismo código ×5 intencional | 1 evento, 4 perdidos | 5/5 | 5/5 |
| 100 scans consecutivos | — | — | 100 entregados, 0 perdidos, 0 duplicados |

Los valores absolutos dependen del host; el objetivo de CI es la comparación
relativa con `compare.py`.

## Pruebas

```powershell
# Transacción + HTTP (rápidas)
python odoo-bin -c odoo.conf -d <db> -u vlux_pos_catalog,vlux_mobile_scanner `
  --test-enable --test-tags /vlux_pos_catalog,/vlux_mobile_scanner --stop-after-init

# Lógica pura del cliente móvil
node --test vlux_mobile_scanner/static/tests/node/scanner_core.test.js
```

Los E2E de navegador son tours de Odoo (`HttpCase`, etiqueta `vlux_e2e`):
`vlux_pos_catalog` (código conocido, repetido ×5, desconocido → alta →
carrito, foto manual → filestore, usuario sin permiso) y
`vlux_mobile_scanner` (entrega, push real por bus, ráfaga de 10, repetido ×5,
POS no listo, entrega duplicada). Requieren `websocket-client` y un Chromium;
en Windows sin Chrome, `tools/dev/edge_devtools_wrapper.cmd` permite usar
Microsoft Edge:

```powershell
$env:VLUX_PYTHON = "C:\Odoo\venv\Scripts\python.exe"
$env:ODOO_BROWSER_BIN = "C:\Odoo\custom_addons\tools\dev\edge_devtools_wrapper.cmd"
```

Resultado actual en el entorno de desarrollo: 21 tests de catálogo, 21 de
scanner (8 previos + 13 de protocolo), 9 unitarios Node, 12 tours de
navegador, 3 de carga del POS y 14 de `vlux_owner` (4 previos + 10 del contrato del dashboard:
día local, comparación, bloques horarios, cajas, top, últimas ventas, stock
bajo, multi-company, queries constantes y caché), todos en verde.

## Integración pendiente

`vlux_pos_catalog` todavía no está en las listas de addons productivos de CI,
`Dockerfile`, `build_release.py`, `vlux_cloud.py`, `vlux_pos.py`,
`Program.cs` ni `smoke_check.py`; se incorporará junto con la división de CI
(fase 8). Hasta entonces se instala manualmente con `-i vlux_pos_catalog`.
