# Golden Path automatizado de la caja PWA (Playwright)

Job de CI `pos-pwa-e2e` · pruebas en `frontend/pos/e2e/` · entorno en `tools/e2e/pwa_e2e_env.py`.

## Qué es

Una prueba de navegador real (Chrome) de la **caja PWA propia** (`/vlux-pos/`). No es el
POS nativo de Odoo: los tours del job `e2e` prueban `/pos/ui` y no cuentan para la PWA.

Corre contra un **Odoo 19 real y desechable**, armado igual que un tenant:

1. Módulos productivos sin datos demo.
2. Plan contable de México, con la misma función que usa la nube (`localisation_script`).
3. Tienda, caja y empleados con `tools/onboarding/configure_store.py`.
4. Datos **sintéticos**: dos productos, un usuario de caja, un token atado a la caja por
   cada vista y una llave de solo lectura para revisar lo que Odoo guardó.

**No se simula la API.** La app es el compilado que Odoo sirve, y las ventas, la apertura,
la devolución y el corte pasan por la API v1 de VLUX y quedan en la base.

## Qué comprueba (dos veces: escritorio 1280×800 y celular 390×844)

| Paso | En la caja | Lo que se lee de vuelta en Odoo |
|---|---|---|
| 1 | Vincular el equipo con el token | — (el token atado a la caja lo emite la semilla) |
| 2 | PIN incorrecto → "PIN incorrecto."; luego el correcto, que revisa el servidor | — |
| 3 | Abrir la caja con $500 | `pos.session` abierta, con fondo de 500 |
| 4 | Venta en línea: 2 refrescos y 1 galleta, pago en efectivo con $100 y cambio | Una sola orden por uuid, pagada, $55.50, sus líneas y cantidades, efectivo, la cajera y "empleado verificado" |
| 5 | **Respuesta perdida**: el primer envío llega a Odoo, pero la respuesta no vuelve a la caja (conexión cortada); la caja reintenta. Pago con tarjeta | Odoo contestó 200 al primer envío; la caja hizo 2 intentos o más; **una sola** orden |
| 6 | **Sin internet**: una venta con la sesión que el servidor abrió (PIN en línea); **recargar la app** sin red (la sirve el service worker); PIN revisado solo en el equipo; otra venta; vuelve la red | Nada llega mientras no hay red; al volver, **exactamente dos** órdenes: la primera "empleado verificado" y la segunda **no verificada** |
| 7 | **Recargar con internet** y volver a entrar | Las órdenes de la sesión son exactamente las ventas hechas (nada reenviado) |
| 8 | **Devolución** de 1 refresco: la cajera ve el candado, el **encargado pone su PIN**; el primer envío pierde la respuesta y la cajera vuelve a tocar *Devolver* | **Una sola** devolución de −$20.00, y `refunded_qty` = 1 en la línea original |
| 9 | **Corte** contando lo esperado ($571.00 en efectivo y $15.50 en tarjeta) | Sesión `closed`, efectivo contado igual al esperado, diferencia 0, sin uuid repetido |

## Qué NO comprueba

- Impresora térmica, cajón, lector de pistola, báscula ni escáner del celular: se prueban en
  el equipo real (guía de pruebas físicas, H01–H10).
- Safari/iOS y Firefox: solo Chrome. La vista de celular es Chrome con viewport, toque y
  `isMobile`, **no** un teléfono real.
- Crédito, entradas y salidas, mayoreo, peso, alta y edición de productos, empleados, dueño
  y cierre automático: tienen pruebas de API (`vlux_pos_api/tests`) y unitarias, pero no
  están en este recorrido.
- Rendimiento con catálogos grandes, varias cajas a la vez y el corte por encima del límite
  con encargado.
- Que la versión **desplegada** sea esta: el job prueba el SHA del commit; lo desplegado se
  verifica aparte (imagen y artefactos servidos).

## Por qué se espera 30 s después de preparar la base

La API de catálogo retiene los registros escritos en los últimos 30 s (`SETTLE_SECONDS` en
`vlux_core/controllers/api_catalog.py`), para que el cursor nunca salte un cambio. Una caja que
se vincula antes ve los productos nuevos hasta su siguiente sincronización, cinco minutos
después. Por eso `prepare` espera 32 s al terminar. En la operación real, un producto dado de
alta en Odoo aparece en la caja en la siguiente sincronización (≤ 5 min).

## Evidencia de que las pruebas detectan fallas (mutaciones)

Se rompió a propósito un flujo crítico, se corrió el Golden Path en una base nueva y se
restauró el código. Los parches no están en el repositorio.

| Mutación | Qué se rompió | Resultado |
|---|---|---|
| M1 | Servidor: se quitó la búsqueda por `uuid` de VLUX antes de registrar la venta | **Sobrevive**, y es correcto: `sync_from_ui` de Odoo vuelve a buscar por `uuid` y `pos.order` tiene `unique (uuid)`. La idempotencia tiene tres capas |
| M1' | Caja: cada reintento de una venta manda un `uuid` nuevo | **Detectada** en el paso 5: la venta nunca termina de enviarse limpia |
| M2' | Caja: cada toque de *Devolver* genera un `uuid` nuevo | **Detectada** en el paso 8: Odoo registró dos devoluciones de −$20 ("one return, not two") |
| M3 | Servidor: el corte guarda $1 más de lo contado | **Detectada** en el paso 9: 572 en lugar de 571 |
| M4 | Caja: ya no pide el PIN del encargado para devolver | **Detectada** en el paso 8: el botón *Devolver* ya no lleva el candado |

## Correrlo en local

Con el Python de Odoo, PostgreSQL local y Chrome o Edge:

```bash
python tools/e2e/pwa_e2e_env.py prepare --odoo-home <odoo> --addons-path "<odoo>/odoo/addons,<odoo>/addons,<repo>" -c <odoo.conf> -d vlux_pwa_e2e_1_test --fixture frontend/pos/e2e/.fixture.json
```

```bash
python tools/e2e/pwa_e2e_env.py serve --odoo-home <odoo> --addons-path "<...>" -c <odoo.conf> -d vlux_pwa_e2e_1_test --port 8279
```

```bash
cd frontend/pos && VLUX_E2E_BASE_URL=http://127.0.0.1:8279 VLUX_E2E_CHANNEL=msedge npm run e2e
```

Reglas de seguridad del script:
- Se niega a usar una base cuyo nombre no termine en `_test`, un PostgreSQL que no sea
  local o una base que ya exista. Cada corrida usa una base nueva y nunca borra nada.
- Las bases de prueba se borran a mano después (con `dropdb` o el administrador de bases).
- Odoo se sirve sin cron (`--max-cron-threads 0`), para que nada corra a espaldas de las
  pruebas, y solo para esa base.

## Secretos

Los tokens, los PIN y la llave de lectura se **generan en cada corrida**, valen solo para la
base desechable y se escriben únicamente en `frontend/pos/e2e/.fixture.json`, que está en
`.gitignore` y no se imprime. En CI la base muere con el job.

Las trazas de Playwright que se guardan como artefacto pueden mostrar esos valores (lo que
se teclea y los encabezados). Por eso solo son válidos en esa base ya destruida. **Nunca** se
usan datos, tokens ni PIN reales.

## Artefactos

`pos-pwa-e2e-report`, 14 días: reporte HTML, `e2e-results/results.json`, y trazas, capturas
y video de lo que falle, además del log de Odoo.
