# Plan de iniciativas: API VLUX v1 y interfaces propias

Plan de ejecución de la dirección acordada en
[ARQUITECTURA_HEADLESS.md](ARQUITECTURA_HEADLESS.md): Odoo como motor detrás de
una API propia, con interfaces VLUX que reemplazan a las de Odoo por etapas.
Este documento dice qué de lo ya construido sirve, qué falta, y en qué orden
hacerlo para que el producto sea escalable, sostenible y operable.

## 1. Qué de lo construido ya sostiene la API

| Pieza existente | Por qué sirve | Qué le falta para la API |
| --- | --- | --- |
| Alta rápida de productos (`vlux_pos_quick_create`) | La lógica, la validación, el bloqueo anti-duplicados y el permiso viven en el servidor, no en la pantalla | Exponerla como endpoint versionado |
| Emparejamiento del escáner | Modelo de autenticación por dispositivo ya probado: token de 256 bits guardado como hash, revocable, con código temporal de un solo uso y límites por minuto | Generalizarlo a "dispositivo = caja" con alcances |
| Canal push por bus | Entrega en tiempo real por canal privado derivado del hash del token | Reutilizarlo como canal de eventos de la API |
| Idempotencia del escáner (`request_id`) | Reintentos de red no duplican ventas ni eventos; un id ajeno se rechaza | Aplicar el mismo patrón al envío de órdenes |
| Dashboard Owner | Agregación en PostgreSQL, caché corta y salida JSON; ya lo consume una interfaz propia | Mover a `/vlux/api/v1` con auth propia |
| `/vlux/ready` y `doctor` | Operabilidad: salud, diagnóstico y verificación por tienda | Añadir métricas por endpoint |
| Perfiles de capacidad y localización al aprovisionar | Cada tienda nace dimensionada y con su país, moneda e impuestos | — |
| Versionado de archivos estáticos | Cada release invalida CDN y navegador: imprescindible para distribuir una interfaz propia | — |
| CI con tours, E2E de nube y presupuestos de rendimiento | Red de seguridad para cambiar sin romper | Añadir pruebas de contrato de la API |

**Conclusión honesta:** los cimientos están. Lo construido no fue trabajo
desechable: la lógica de negocio ya está del lado del servidor, el modelo de
autenticación por dispositivo está probado en producción de staging, y la
disciplina de pruebas y despliegue existe. Lo que falta es el **contrato**.

## 2. Brechas que hay que cerrar

| # | Brecha | Impacto si no se cierra |
| --- | --- | --- |
| ~~B1~~ | ~~No hay espacio de nombres versionado ni formato único de error~~ (cerrada en Fase A) | Cada interfaz nueva inventa su propio contrato y se rompe en cada actualización de Odoo |
| ~~B2~~ | ~~No hay autenticación de cliente propia~~ (cerrada en Fase A; falta migrar Owner a ella) | Sin login propio no hay interfaz propia |
| ~~B3~~ | ~~No hay sincronización de catálogo con cursor ni bajas explícitas~~ (cerrada en Fase B) | Una caja propia no puede mantener su copia local al día |
| B4 | No hay envío de órdenes por API | Sin esto no hay venta desde una interfaz propia |
| B5 | No hay apertura ni cierre de caja por API | No se puede operar un día completo |
| B6 | No hay cálculo de precios e impuestos expuesto | El total cobrado podría no coincidir con el contabilizado |
| B7 | No hay OpenAPI ni pruebas de contrato | Nada impide romper a los clientes sin darse cuenta |
| B8 | No hay métricas por endpoint | No se puede operar ni dimensionar lo que no se mide |

## 3. Iniciativa 2 — API VLUX v1

Cuatro fases. Cada una entra a `main` con sus pruebas y no rompe nada existente.

### Fase A — Cimientos del contrato — **entregada** (`vlux_core 19.0.1.3.0`)

Documentada en [API_V1.md](API_V1.md).

- Espacio `/vlux/api/v1` con envoltura única de respuesta y errores
  (`{"ok": false, "error": "<CODIGO>", "message": "..."}`), identificador de
  petición en cada respuesta y en `X-Request-Id`, y `no-store` en todas.
- Autenticación con token `vlux.api.token`, derivada del modelo del escáner:
  solo se guarda el hash, el texto plano se muestra una vez, es revocable, puede
  expirar y está limitado a 600 solicitudes por minuto.
- Mapa de roles VLUX a alcances: un token nunca puede hacer más que su usuario.
- Emisión y revocación desde **Ajustes → API VLUX**, sin entrar al shell.
- OpenAPI publicado en `/vlux/api/v1/openapi.json` con la versión del addon.
- Límite de solicitudes unificado (`vlux.rate.limit`) para API y Owner.
- **Criterio de aceptación cumplido:** un cliente sin sesión de Odoo se
  autentica con `GET /me`, es rechazado fuera de su alcance (403) y el contrato
  está cubierto por 11 pruebas que fallan si cambia.

### Fase B — Datos de la tienda — **entregada** (`vlux_core 19.0.1.4.0`, `vlux_pos_catalog 19.0.1.2.0`)
- Catálogo con sincronización incremental: cursor opaco `(vlux_sync_date, id)`
  (el sello avanza con la variante o su plantilla), páginas acotadas y
  **bajas explícitas**: archivados y retirados del POS llegan con sus banderas,
  los borrados de verdad como lápidas (`vlux.catalog.tombstone`, 90 días).
- Clientes (feed con cursor), listas de precios, impuestos, categorías y
  `/store/config`.
- Alta rápida de productos como `POST /catalog/products`.
- B.1: `image_version` en el feed y `GET /catalog/products/<id>/image`
  (miniaturas de Odoo, `ETag`/`304`, caché privada).
- **Criterio de aceptación cumplido:** 10 000 productos en 21 páginas de 500,
  p95 = 121 ms por página, 10 consultas por página constantes; tras editar 50 y
  borrar 5, la sincronización incremental entrega exactamente esos 50 y 5
  (`tools/perf/bench_catalog_sync.py`). Contrato en `docs/API_V1.md` §4.1.

### Fase C — Operación de venta
- Apertura y cierre de caja, con control de efectivo.
- Envío de órdenes como envoltorio de `pos.order.sync_from_ui`, con `uuid`
  generado por el cliente para que un reintento nunca duplique una venta.
- Cálculo de precios e impuestos del lado del servidor, con redondeo mexicano.
- Devoluciones y descuentos.
- **Criterio de aceptación:** una venta enviada dos veces por mala conexión
  produce una sola orden; el total cobrado coincide al centavo con el
  contabilizado; el corte de caja cuadra.

### Fase D — Operabilidad de la API
- Métricas por endpoint (latencia, errores, volumen) y registro estructurado.
- Sección API en `doctor` y presupuestos de rendimiento en el CI nocturno.
- **Criterio de aceptación:** una caída de rendimiento o un aumento de errores
  se detecta desde el diagnóstico, sin entrar a la base.

## 4. Iniciativa 3 — POS VLUX mínimo

- Flujo minorista completo: escanear, carrito, cobro en efectivo y tarjeta,
  ticket, y **cola sin conexión** con la misma idempotencia del escáner.
- Convive con el POS de Odoo, activado por caja, en la tienda piloto.
- **Criterio de aceptación:** un día completo de operación simulada, incluido un
  corte de red de 30 minutos, sin perder ni duplicar ventas.

## 5. Iniciativa 4 — Paridad y cambio

- Impresión de tickets y cajón, pantalla de cliente, control de efectivo
  completo, informes de cierre.
- CFDI cuando `vlux_facturacion` deje de ser simulación.
- El POS VLUX pasa a ser el predeterminado; el de Odoo queda como respaldo.

## 6. Lo que corre en paralelo (y no depende de la API)

| Qué | Por qué no puede esperar |
| --- | --- |
| ~~Carga masiva de catálogo~~ (entregada: `docs/IMPORTAR_CATALOGO.md`) | Ninguna tienda arranca capturando miles de productos a mano |
| Impresión de tickets y cajón | Sin ticket no hay venta mostrador (software ya preparado) |
| Corte de caja y devoluciones | Operación diaria del negocio |
| Modo sin internet del POS de Odoo | Protege a las tiendas hasta que exista el POS propio |
| Respaldos automáticos y alertas | Requisito para operar con datos reales |

## 7. Reglas que mantienen esto sostenible

1. **Lógica de negocio solo en el servidor.** Las pantallas llaman, no deciden.
2. **El contrato se rompe solo con una versión nueva.** `v1` no cambia de forma
   incompatible; lo incompatible es `v2`.
3. **Todo endpoint nace con pruebas de contrato** y aparece en OpenAPI.
4. **Toda ruta pública se audita**: sin sesión, con token falso, fuera de
   alcance, con entrada inválida.
5. **Cada release versiona sus archivos estáticos**, para que la CDN y los
   dispositivos no sirvan código viejo.
6. **Lo que no se mide no se opera:** presupuestos en CI y diagnóstico en campo.

## 8. Decisiones pendientes del negocio

| # | Decisión | Bloquea |
| --- | --- | --- |
| D1 | Tecnología y empaquetado de las interfaces propias (navegador instalable, escritorio o móvil) | Iniciativa 3 |
| D2 | Estrategia de hardware: impresora de red por caja | Impresión y cajón |
| D3 | CFDI: PAC y si el POS debe timbrar desde el inicio | Iniciativa 4 |
| D4 | Modelo de precios y soporte por tienda | Release comercial |

## 9. Orden recomendado

1. ~~Fase A de la API (contrato y autenticación).~~ **Hecha.**
2. **En curso:** carga masiva de catálogo y corte de caja (operación).
3. **Después:** Fase B (catálogo por API) y modo sin internet del POS de Odoo.
4. **Luego:** Fase C (venta por API) y, con ella, el POS VLUX mínimo.
5. **Al final:** Fase D, paridad de hardware y CFDI.

`PRODUCTION_GO=NOT_YET` hasta cerrar lo de la sección 6 y validar en tienda.
