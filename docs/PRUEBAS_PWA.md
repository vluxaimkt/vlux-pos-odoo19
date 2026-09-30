# Pruebas físicas del POS VLUX (PWA)

Guía para probar la caja propia en un equipo real, igual que las pruebas del
POS de Odoo. Cada prueba dice qué hacer y qué debe pasar; anota **PASA** o
**FALLA** (con captura o descripción) y el folio de las ventas.

Detalle técnico en [POS_PWA.md](POS_PWA.md). La PWA convive con el POS de
Odoo: usan la misma sesión de caja, así que si la caja ya está abierta en uno,
el otro vende en esa misma sesión.

## Antes de empezar

**Equipo:** PC o tablet con **Chrome o Edge actualizados** (versión 111 o más
nueva). Opcional: lector de código USB, impresora de tickets de 80 mm.

**1. Emitir el token de la caja** (una sola vez, como dueño o administrador):

1. En Odoo: **Ajustes → API VLUX → Emitir token**.
2. Nombre: `Caja Mexico — PWA`. Usuario: la cajera o el cajero (rol VLUX
   *Cashier*). **Caja:** `Caja Mexico`. Renovación: `30` días.
3. Alcances: marcar **exactamente** *Estado del sistema*, *Leer catálogo y
   precios*, *Registrar ventas*, *Abrir y cerrar caja*. Nada más.
4. **Emitir token** y copiarlo. Se muestra una sola vez; no lo mandes por
   chat ni correo: pégalo directo en el equipo de la caja.

**2. Abrir e instalar la app:** en el equipo, abrir
`https://pos-demo.vlux.com.mx/vlux-pos/`. Instalarla con el ícono de
"Instalar" de la barra de direcciones (o menú → *Instalar VLUX POS* / *Agregar
a pantalla de inicio*).

**3. Precios con IVA incluido.** Vender **sin internet** sólo se permite si los
precios de los productos ya incluyen el IVA (lo normal en tiendas en México).
Si en la tienda los precios se capturan sin IVA, la caja pedirá internet para
cobrar (prueba 8). Confirma con el dueño cómo están capturados.

## Pruebas

| # | Prueba | Qué hacer | Qué debe pasar |
| --- | --- | --- | --- |
| 1 | Vincular | Pegar el token, *Conectar* | Dice "Este equipo será Caja Mexico"; al aceptar descarga el catálogo y muestra la pantalla de venta o la de PIN |
| 2 | Token equivocado | Probar un token con permisos de más (p. ej. con *Alta y edición de productos*) o sin caja | La app lo rechaza y dice por qué; no entra |
| 3 | PIN | Elegir empleado, poner el PIN mal 5 veces, luego bien | Tras 5 errores se bloquea 30 s; con el PIN correcto entra |
| 4 | Abrir caja | Si la caja está cerrada: fondo inicial (p. ej. 500) → *Abrir caja* | Pasa a la pantalla de venta; en Odoo la sesión de *Caja Mexico* aparece abierta |
| 5 | Venta en efectivo | Escanear un producto 2 veces, buscar otro por nombre y tocarlo, *Cobrar*, tocar un billete (p. ej. 200), *Terminar venta* | Carrito con cantidad 2 en el primero; cambio correcto; ticket con folio del servidor; en Odoo (*Punto de venta → Pedidos*) la venta con ese folio, pagada |
| 6 | Tarjeta y mixto | Una venta con *Tarjeta*; otra con parte en tarjeta y el resto en efectivo | Tarjeta cobra exacto (nunca de más); en la mixta el cambio sale sólo del efectivo |
| 7 | Código desconocido | Escanear un código que no existe | Aviso "No se encontró…"; no agrega nada |
| 8 | Sin internet | Con la app ya abierta al menos una vez con internet: desconectar Wi-Fi/cable, vender un producto con IVA incluido | Ticket con "folio pendiente"; arriba "1 por enviar". Al reconectar se envía sola (o *Enviar ahora*), el ticket/cola muestra el folio y en Odoo hay **una sola** venta |
| 9 | Sin internet, total no exacto | Sin internet, cobrar un producto cuyo precio no incluye IVA (si existe) | La caja no cobra: pide internet |
| 10 | Recargar | Con productos en el carrito, recargar la página (F5) o cerrar y abrir la app | El carrito sigue igual |
| 11 | Ticket impreso | *Imprimir ticket* | Sale sólo el ticket, a 80 mm, con RFC, régimen, leyenda y folio |
| 12 | Corte con faltante | Menú → *Corte de caja*; como cajera, contar $50 menos de lo esperado → *Cerrar caja* | Aviso de que supera $30 y el servidor lo rechaza; **la caja sigue abierta** |
| 13 | Corte correcto | Contar lo esperado → *Cerrar caja* → *Imprimir corte* | Hoja de corte con esperado/contado/diferencia por forma de pago; la app vuelve a "Abrir caja"; en Odoo la sesión cerrada con su asiento |
| 14 | Corte con ventas sin enviar | Sin internet, vender; al volver la red, antes de que se envíe, abrir *Corte de caja* | No deja cerrar hasta enviar las ventas (*Enviar y volver a calcular*) |
| 15 | Inactividad | No tocar la caja 5 minutos | Vuelve a pedir PIN |
| 16 | Desvincular | Menú → *Desvincular equipo* | Pide token otra vez; las ventas por enviar se conservan |
| 17 | Revocar | En Odoo revocar el token (Ajustes → API VLUX → Tokens) y esperar a la siguiente sincronización (≤ 5 min) o recargar | La app avisa que el token fue revocado |
| 18 | Instalada y sin red | Cerrar la app instalada, desconectar internet, abrirla | Abre y deja vender (con precios con IVA incluido) |

## Si algo falla

- Anota la hora, qué hiciste y qué salió; si hay venta, el folio o los
  primeros 8 caracteres que salen al pie del ticket.
- Las ventas que el servidor rechaza quedan en **Menú → Ventas por enviar**
  como *Por revisar*, con el motivo. **Nunca se borran solas.**
- El POS de Odoo sigue disponible como respaldo en todo momento.
