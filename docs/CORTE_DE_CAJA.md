# Corte de caja y devoluciones

El día de una caja con el POS: apertura con fondo, ventas, retiros,
devoluciones y cierre con arqueo. La lógica es la de Odoo; VLUX añade que el
límite de diferencia del arqueo lo haga cumplir el **servidor**, no sólo la
pantalla (ver al final).

## Quién puede qué

Con inicio de sesión por empleado (`pos_hr`, activo en las cajas VLUX), el
rol que cuenta es el del empleado que está en la caja:

| Acción | Cajero | Supervisor, Administrador, Dueño |
| --- | --- | --- |
| Abrir la caja y declarar el fondo | sí | sí |
| Vender, cobrar en efectivo o tarjeta | sí | sí |
| Devolver un ticket (total o parcial) | sí (*) | sí |
| Retiro o ingreso de efectivo | **no** | sí |
| Cerrar con diferencia dentro del límite | sí | sí |
| Cerrar con diferencia **mayor** al límite | **no** | sí |

(*) Odoo permite devolver a cualquier cajero, salvo a los empleados que la
caja marque con acceso *mínimo*. Si la tienda quiere que las devoluciones las
autorice un supervisor, se marca a los cajeros como *mínimos* en la
configuración de la caja (decisión D5 del seguimiento).

Los usuarios con rol VLUX Supervisor o superior son gerentes del POS en Odoo, y
Odoo los agrega solo a la lista de gerentes de cada caja.

## El día

1. **Apertura.** *Abrir caja*: se cuenta el fondo inicial y se confirma.
2. **Ventas.** Efectivo, tarjeta o combinadas. El cambio se calcula solo.
3. **Retiros e ingresos** (supervisor): menú ☰ → *Entrada/Salida de efectivo*,
   con monto y motivo. Quedan en el reporte del corte.
4. **Devoluciones**: ☰ → *Pedidos* (o *Reembolso*), se elige el ticket, la
   cantidad a devolver y se paga la devolución (normalmente en efectivo). La
   devolución queda ligada al ticket original, no se puede devolver más de lo
   vendido, y la mercancía regresa al inventario.
5. **Cierre**: ☰ → *Cerrar caja*. La pantalla muestra lo que debería haber:
   fondo + ventas en efectivo − devoluciones en efectivo ± retiros e ingresos.
   Se captura lo contado (y, si aplica, lo de tarjeta). La diferencia se
   registra contablemente y queda en la nota del cierre.
6. **Reporte** (*Venta diaria*, el "corte Z"): totales por método de pago,
   impuestos, productos, devoluciones y movimientos de efectivo. Se imprime o
   descarga desde el cierre o desde el backend (*Punto de venta → Reportes*).

## Límite de diferencia del arqueo

En la configuración de la caja: *Establecer diferencia máxima* y el monto
permitido. Si lo contado difiere más que eso, sólo un supervisor puede cerrar;
el cajero ve: *"La diferencia del corte ($4.00) supera la permitida ($2.00).
Vuelve a contar o pide a un supervisor que cierre la caja."*

**Recomendación VLUX:** activarlo en todas las cajas. El monto es decisión de
cada tienda (D6); algo entre $20 y $50 MXN es habitual.

### Por qué lo valida el servidor

Odoo aplica este límite sólo en la ventana de cierre (JavaScript): la llamada
del servidor que cierra la sesión acepta cualquier diferencia. Una interfaz
propia, una petición repetida o un cliente modificado se lo saltarían. VLUX
aplica la misma regla en `pos.session.close_session_from_ui`
(`vlux_core/models/pos_session.py`): por encima del límite, sólo cierra un
gerente del POS; con inicio por empleado, el que cuenta es el empleado de la
sesión, no quien tenga abierta la sesión del navegador.

## Qué se prueba

- `vlux_core/tests/test_register_day.py`: un día completo con las mismas
  llamadas que hace la pantalla. Fondo 500, ventas en efectivo 56 y 18,
  tarjeta 60, retiro 100, devolución de 18 en efectivo: el esperado es 456;
  se cuentan 452 y el cierre registra −4 con el asiento cuadrado; la
  devolución está ligada a su ticket; el inventario sube 1; el reporte Z da
  efectivo 56 y tarjeta 60. Además: el límite lo impone el servidor (también
  para diferencias de tarjeta), un gerente sí puede cerrar por encima, y un
  cajero no puede retirar efectivo.
- `vlux_pos_catalog/tests/test_tours.py::test_register_day_refund_and_close`:
  el mismo flujo en el navegador con los parches VLUX cargados (venta por
  escaneo, devolución parcial desde la lista de pedidos, cierre).
