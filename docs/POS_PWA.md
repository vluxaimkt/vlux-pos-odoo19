# POS VLUX como PWA

La caja propia de VLUX (iniciativa 3 de [PLAN_INICIATIVAS.md](PLAN_INICIATIVAS.md)):
una app web instalable que vende con la [API v1](API_V1.md) y sigue vendiendo
sin internet. Convive con el POS de Odoo; la lógica de negocio (precios,
impuestos, sesiones, corte, contabilidad) vive en el servidor.

## Estado

**Estructura (primera entrega).** Funciona hoy:

- Vincular el equipo a una caja con un token de la API (Ajustes → API VLUX →
  Emitir token, eligiendo la caja; alcances `system:read catalog:read
  orders:write session:manage`). El token decide qué caja es el equipo.
- Descargar el catálogo y los clientes a IndexedDB con sincronización
  incremental (cursor, bajas, `RESYNC_REQUIRED`), cada 5 minutos con red.
- Inicio de sesión del empleado con PIN **sin red** (cajas con `pos_hr`).
- Abrir la caja con el efectivo inicial.
- Buscar productos en la copia local por nombre (sin acentos, por prefijos) o
  por código de barras; **lector de código**: escribe el código y Enter lo
  agrega al carrito.
- **Carrito** que sobrevive a recargar la app: cantidades, quitar, cancelar.
- **Cobro** en efectivo (montos rápidos, cambio) y tarjeta, o mixto. El
  cambio sólo sale del efectivo, igual que en el servidor.
- **Ticket** imprimible a 80 mm con los datos fiscales de la tienda; muestra
  "folio pendiente" sin internet y el folio del servidor cuando llega.
- **Ventas por enviar / por revisar** visibles en pantalla, con "Enviar ahora"
  y "Reintentar".
- Se instala (manifest) y abre sin red (service worker).

- **Corte de caja:** efectivo esperado (fondo + ventas + movimientos) y cada
  forma de pago, lo contado y la diferencia en vivo; aviso anticipado del límite
  ($30): por encima sólo cierra un encargado (lo decide el servidor, la caja
  sigue abierta si lo rechaza); no deja cerrar con ventas sin enviar (no
  estarían en el corte); hoja de corte imprimible a 80 mm con firma.

- **Clientes y crédito (fiado)**, como en el POS de Odoo (D7): lista de
  clientes con lo que deben, su límite y lo disponible, búsqueda por nombre o
  teléfono, alta de cliente; el encargado autoriza crédito y fija el límite;
  venta a crédito ("Fiar a…", sólo encargado y con cliente elegido, respeta el
  límite) con ticket **VENTA A CRÉDITO** (saldo anterior, esta compra, saldo
  nuevo, firma); **abonos** de cualquier cajera en efectivo o tarjeta, con
  "Liquidar todo" y ticket **ABONO A CUENTA** ("CUENTA LIQUIDADA"). El corte
  muestra el fiado del día aparte del efectivo.

**Falta en la PWA:** descuentos, devoluciones y entradas/salidas de efectivo:
siguen en el POS de Odoo.

## Cómo se decide el total

- **Con internet**, el total lo cotiza el servidor (`POST /orders/quote`) con
  el mismo motor de impuestos que contabiliza; la venta se envía con esos
  precios y con `expected_total`.
- **Sin internet**, la caja calcula el total con su copia del catálogo y de
  los impuestos, **sólo si es exacto al centavo**: todos los impuestos de la
  venta son porcentajes incluidos en el precio (IVA incluido, lo normal en
  México) y la caja no usa listas de precios. Si no, la caja pide internet en
  vez de cobrar una cantidad que el servidor contabilizaría distinta.
- El servidor rechaza (queda "por revisar", nunca se pierde) una venta cuyo
  total no coincide con `expected_total`. Crédito, abonos,
descuentos y devoluciones siguen en el POS de Odoo (ver `VLUX_SEGUIMIENTO`: M26,
M27).

**Pruebas físicas:** [PRUEBAS_PWA.md](PRUEBAS_PWA.md).

## Dónde está cada cosa

| Ruta | Qué |
| --- | --- |
| `frontend/pos/` | Código fuente (Preact + TypeScript + Vite, Tailwind + daisyUI, Dexie) |
| `frontend/pos/src/api/` | Cliente tipado de la API v1 y sus tipos |
| `frontend/pos/src/db/` | Base local (Dexie): productos, clientes, `meta`, `outbox`; búsqueda |
| `frontend/pos/src/sync/` | Sincronización del catálogo y cola de ventas |
| `frontend/pos/src/screens/` | Pantallas: vincular, PIN, abrir caja, vender |
| `frontend/pos/src/sw.ts` | Service worker (Workbox) |
| `vlux_pos_app/` | Addon de Odoo que sirve la app en `/vlux-pos/` |
| `vlux_pos_app/static/dist/` | **Compilado**, guardado en git |

`vlux_pos_app` sirve la página, el service worker (`/vlux-pos/sw.js`, alcance
`/vlux-pos/`) y el manifest. Son públicos porque no tienen datos: todo lo que
la caja lee o escribe pasa por la API con el token del equipo, nunca por el
login de Odoo. La página lleva una CSP estricta (`script-src 'self'`, sin
`unsafe-inline`), por eso la app no usa estilos en línea.

## Sin internet

- **La app** (página, JS, CSS) queda en la caché del service worker: se abre
  sin red desde el segundo arranque. Verificado en Edge sin interfaz (se
  registra, precarga y abre con la red cortada).
- **Los datos** (catálogo, clientes, empleados, estado de la caja, ventas por
  enviar) viven en IndexedDB, nunca en la caché del worker; la API responde
  `no-store`.
- **Las ventas** se guardan primero en la cola local con su `uuid` y luego se
  envían. Reenviar es inofensivo: el servidor registra una sola vez. Un error
  de red o del servidor se reintenta con espera creciente (2 s … 1 min); un
  rechazo del contrato (pago corto, total distinto, caja cerrada) queda
  **para revisión**, nunca se borra.
- Abrir y cerrar la caja requieren red.

## Seguridad

**Sin secretos en el código.** El compilado no contiene tokens, contraseñas,
claves ni direcciones de servidor: la app habla sólo con el servidor que la
sirvió (`/vlux/api/v1`, mismo origen). El token de cada caja se captura en el
equipo al vincularlo, nunca se compila. Una prueba de Odoo revisa el compilado
servido en busca de direcciones y credenciales.

**Encabezados que sirve `vlux_pos_app`** (probados en `tests/test_routes.py`):

| Medida | Para qué |
| --- | --- |
| CSP `default-src 'none'`, `script-src 'self'`, `connect-src 'self'`, sin `unsafe-inline` ni `unsafe-eval` | Ningún script de terceros ni en línea; el token sólo puede viajar a este servidor |
| Trusted Types (`require-trusted-types-for 'script'`, política única `vlux-pos`) | Un texto inyectado no puede convertirse en código; la única URL de script permitida es el service worker |
| `frame-ancestors 'none'`, `X-Frame-Options: DENY` | Nadie puede incrustar la caja en otra página (clickjacking) |
| `Cross-Origin-Opener-Policy` y `Cross-Origin-Resource-Policy: same-origin` | Aísla la ventana y los archivos de otros sitios |
| `Permissions-Policy` con todo apagado (cámara, micrófono, USB, pagos, ubicación…) | Mínimo privilegio; el hardware abrirá sólo lo que use |
| `Referrer-Policy: no-referrer`, `nosniff` | No filtra direcciones; el navegador no reinterpreta archivos |
| `upgrade-insecure-requests` cuando se sirve por HTTPS | Nada viaja sin cifrar |

HSTS lo pone el frente TLS (Caddy, Cloudflare), no Odoo: una instalación local
con certificado propio no debe quedar bloqueada por él.

**En la app:**

- **Mínimo privilegio del token:** la caja rechaza un token al que le falte un
  permiso **o que tenga permisos de más** (`catalog:write`, `dashboard:read`),
  y uno que **no esté atado a una caja**: el servidor no deja usarlo en otra.
  Si el equipo se pierde, el token no sirve para editar el catálogo, ver el
  negocio ni operar otra caja, y se revoca en Odoo.
- **Renovación automática:** el token de una caja caduca a los 30 días y la
  app lo renueva sola cuando le quedan menos de 15 (`POST /token/rotate`); un
  token robado deja de servir por sí solo.
- **Formato del token** revisado antes de enviarlo: un pegado equivocado (una
  contraseña, un texto) nunca sale del equipo.
- **PIN:** tras 5 PIN incorrectos seguidos el teclado se bloquea 30 s, luego
  60 s, 2 min… hasta 15 min; el bloqueo sobrevive a recargar la app.
- **Bloqueo por inactividad:** a los 5 minutos sin tocar la caja vuelve a pedir
  PIN.
- **Desvincular el equipo** borra de él el token, el catálogo, los clientes y
  los empleados con sus PIN; sólo conserva las ventas por enviar.
- **El service worker** guarda sólo la página y los archivos compilados; nunca
  respuestas de la API.
- **Cadena de suministro:** en producción sólo viajan Preact y Dexie. El CI
  corre `npm audit` (falla con vulnerabilidades altas) y `npm audit signatures`
  (firmas del registro de npm), instala con `npm ci` desde el lockfile, y
  Dependabot propone actualizaciones cada semana.

Todo lo anterior se verificó en Edge sin interfaz: el service worker se
registra bajo Trusted Types, sin ninguna violación de la política, y la app
abre con la red cortada.

**Riesgos conocidos (aceptados o pendientes):**

- El token vive en IndexedDB del navegador: un script que lograra ejecutarse
  en el mismo origen podría leerlo. Lo impiden la CSP y Trusted Types; el daño
  lo limitan el mínimo privilegio y la revocación.
- El PIN se valida sin red con su SHA-1, como en el POS de Odoo (F35).
- Un equipo apagado más de 30 días tiene que volver a vincularse con un token
  nuevo (su token caducó).

## Desarrollo

Requiere Node 22 o más reciente.

```bash
cd frontend/pos
npm ci
npm test            # pruebas unitarias (Vitest, IndexedDB simulado)
npm run typecheck   # TypeScript estricto (app y service worker)
npm run dev         # servidor de Vite; /vlux se envía al Odoo local en 8069
npm run build       # compila a vlux_pos_app/static/dist
```

**Después de cambiar el código fuente hay que compilar y commitear
`vlux_pos_app/static/dist`.** El job `pos-pwa` del CI vuelve a compilar y falla
si el compilado guardado no coincide con el código. Así los instaladores
(Windows, Ubuntu) y la imagen de la nube no necesitan Node.

Las pruebas de Odoo del addon (`vlux_pos_app/tests`) comprueban que la página,
el worker con su alcance, el manifest, los iconos y cada archivo compilado que
la página menciona se sirvan, y que el worker lleve su lista de precarga.
