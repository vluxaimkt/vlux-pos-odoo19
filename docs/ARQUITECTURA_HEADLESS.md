# Decisión de arquitectura: Odoo como backend, interfaces VLUX propias

| Campo | Valor |
| --- | --- |
| Estado | Aceptada (2026-09-18) |
| Alcance | Dirección de producto a largo plazo para VLUX POS |
| Relacionado | [Arquitectura](ARQUITECTURA.md), [Operabilidad y rendimiento](OPERABILIDAD_PERFORMANCE.md), [Plan de iniciativas](PLAN_INICIATIVAS.md), [API VLUX v1](API_V1.md) |

## Contexto

VLUX POS es el producto vendible. Hoy la caja es la interfaz estándar del POS de
Odoo 19 (OWL) extendida con parches (`vlux_pos_catalog`, `vlux_mobile_scanner`).
Otras superficies ya son propias y sólo consumen JSON de Odoo: la PWA de
`vlux_owner` y el cliente móvil del escáner.

El objetivo a largo plazo es que el cliente no vea la interfaz de Odoo y que
Odoo quede como motor de negocio. Opciones evaluadas:

| Opción | Ventaja | Problema |
| --- | --- | --- |
| A. Seguir extendiendo la UI del POS de Odoo | Rápido; offline, impresión y cierres ya resueltos | Odoo reescribe el frontend del POS en casi cada versión mayor: los parches se rompen en cada actualización. Marca y experiencia limitadas |
| B. Reescribir POS y administración de golpe | Producto 100 % propio | Meses rehaciendo offline, impuestos/redondeos, sesiones, devoluciones y hardware sin nada vendible en medio. Riesgo máximo |
| **C. Migración gradual (strangler)** | Cada etapa entrega valor; el riesgo se reparte | Exige disciplina: no seguir poniendo lógica en la UI de Odoo |

## Decisión

Se adopta la **opción C**:

```text
[POS VLUX]   [Owner]   [Escáner]   [Admin VLUX (futuro)]     interfaces propias
      \         |          |          /
       +--------+----------+---------+
                VLUX API v1                                   contrato estable y versionado
                     |
   Odoo 19 = motor: contabilidad, inventario, impuestos,      fuente única de verdad
   sesiones de caja, seguridad, bus, PostgreSQL, filestore
```

### Reglas

1. **Lógica de negocio sólo en el servidor.** Validaciones, permisos, precios,
   impuestos y efectos en inventario viven en modelos/servicios de los addons.
   Los parches OWL existentes se limitan a presentación y a llamar al servidor;
   así la misma lógica sirve al POS de Odoo hoy y al POS VLUX mañana.
2. **Contrato estable `/vlux/api/v1`.** JSON documentado (OpenAPI), errores
   uniformes (`{"ok": false, "error": "<CODIGO>"}` con estado HTTP coherente),
   autenticación propia de usuario y de dispositivo (modelo del emparejamiento
   del escáner: tokens hasheados, revocables, con alcance), límites de uso y
   versionado explícito. Un cambio incompatible es `v2`, nunca una rotura de
   `v1`. Las actualizaciones de Odoo se absorben detrás del contrato.
3. **Las ventas entran por el camino nativo.** El POS VLUX envía órdenes en el
   formato que consume `pos.order.sync_from_ui`, con `uuid` generado en el
   cliente (idempotencia ante reintentos y modo offline). Contabilidad,
   inventario y cierres siguen siendo los de Odoo; no se reimplementan.
4. **Se reemplaza por superficie, de menor a mayor riesgo.** Una interfaz nueva
   convive con la de Odoo hasta alcanzar paridad y se activa por caja o por
   tenant. El periodo de doble mantenimiento debe ser corto y con fecha.
5. **Odoo sigue siendo la única fuente de verdad.** Sin bases paralelas ni
   sincronizaciones a otros almacenes; las cachés son derivadas y desechables.

## Estado actual por superficie

| Superficie | Interfaz | Acceso a datos | Estado frente a la decisión |
| --- | --- | --- | --- |
| Owner | PWA propia (`/vlux-owner/`) | `/vlux_owner/api/dashboard` (sesión) y `/vlux_owner/api/share/dashboard` (bearer) | Alineado; falta mover a `/vlux/api/v1` y login propio |
| Escáner móvil | Cliente propio (`/vlux/scanner`) | `/vlux/mobile/*` con token de dispositivo | Alineado; modelo de auth reutilizable |
| Alta rápida de productos | Diálogo OWL dentro del POS de Odoo | RPC a `product.template` (`vlux_pos_quick_create`, validación en servidor) | Lógica ya en servidor; falta endpoint de API |
| Caja (venta, cobro, ticket) | POS de Odoo | RPC interno del POS | Pendiente (iniciativa 3) |
| Configuración y back office | Backend de Odoo | ORM | Último en migrarse; puede quedar para contadores y soporte |
| Login | `/web/login` de Odoo | Sesión de Odoo | Pendiente (iniciativa 2) |

## Hoja de ruta

| Etapa | Contenido | Entregable |
| --- | --- | --- |
| Iniciativa actual (fases 5–8) | Cerrar operabilidad y rendimiento. La fase 5 prioriza la capa de datos (carga de catálogo, sincronización incremental por `write_date`, consultas) porque sirve a ambos POS; en la UI de Odoo sólo mejoras de bajo coste | Producto estable para pilotos |
| Iniciativa 2 — VLUX API v1 | Autenticación de usuario/dispositivo sin `/web/login`; catálogo con sincronización incremental (cursor `(write_date, id)`, bajas explícitas de productos archivados o retirados del POS, páginas acotadas; el POS de Odoo ya lo hace con `pos_last_server_date` + `filter_local_data` y sirve de referencia); clientes; listas de precios e impuestos; apertura y cierre de caja; envío de órdenes (envoltorio de `sync_from_ui`); inventario; OpenAPI y tests de contrato | Base para cualquier interfaz |
| Iniciativa 3 — POS VLUX mínimo | Flujo minorista completo: escanear, carrito, cobro efectivo/tarjeta, ticket, cola offline con `uuid`. En paralelo al POS de Odoo en la tienda piloto | Primera caja propia sin riesgo |
| Iniciativa 4 — Paridad y cambio | Devoluciones, descuentos, control de efectivo, impresoras y cajón, CFDI (cuando `vlux_facturacion` deje de ser simulación). POS VLUX por defecto; POS de Odoo como respaldo | Caja 100 % VLUX |
| Posterior | Administración VLUX para las tareas del dueño | Odoo invisible para el cliente |

## Decisiones pendientes (antes de la iniciativa 3)

- **Stack de las interfaces.** Las reglas de la iniciativa actual prohíben un
  "React paralelo" como fuente de verdad; una interfaz que sólo consume la API
  no lo es, pero el framework, el empaquetado (PWA, escritorio o móvil) y el
  soporte offline se decidirán en un documento propio.
- **Hardware.** Impresora de tickets, cajón y báscula: agente local (el
  instalador Windows ya incluye un service host) frente a WebUSB/WebSerial.
- **Cálculo de impuestos en el cliente.** Reutilizar la lógica de impuestos de
  Odoo o calcular en servidor con recálculo final; el total cobrado debe
  coincidir exactamente con el contabilizado (IVA/IEPS y redondeo mexicano).
- **Autenticación.** Flujo de login propio, sesiones de dispositivo por caja y
  cómo se relacionan con los roles VLUX existentes.

## Consecuencias

- Las actualizaciones mayores de Odoo exigen portar addons de servidor y
  mantener el contrato de la API, no rehacer interfaces.
- La API añade una capa que mantener: versionado, documentación y tests de
  contrato son obligatorios desde la primera ruta.
- La capacidad sigue dependiendo de Odoo y PostgreSQL; escala por instancia de
  tenant (`vlux-cloud`) y por perfiles de capacidad, no por la API.
- Licencias: Odoo Community es LGPL-3. Los addons que dependen de Odoo siguen
  bajo LGPL; una interfaz que sólo consume la API por HTTP puede tener su
  propia licencia.
