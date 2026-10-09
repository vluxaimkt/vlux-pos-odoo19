# Hardware de la caja VLUX POS

Qué periféricos soporta VLUX POS, cómo se conectan y qué está verificado hoy.
El software está preparado para todos ellos: conectar el equipo no requiere
cambios de código.

## Resumen

| Periférico | Cómo se conecta | Estado |
| --- | --- | --- |
| Lector de códigos USB (pistola) | Directo a la PC, modo teclado | ✅ Cubierto por tours automáticos |
| Escáner con el celular (VLUX) | Emparejado con el QR de la caja | ✅ Validado con teléfonos reales |
| Impresora de tickets Epson ePOS | Red (Ethernet o Wi-Fi), sin caja IoT | ⚙️ Configuración lista; falta probar con impresora física |
| Cajón de dinero | Cable RJ11/RJ12 al puerto DK de la impresora | ⚙️ Se abre desde la impresora; falta prueba física |
| Pantalla de cliente | Segunda pantalla o navegador | ⚙️ Soportado por Odoo; falta prueba con monitor |
| Báscula con cable (Torrey PCR-40T u otra RS-232/USB) | Cable serial/USB a la PC de la caja VLUX (Chrome/Edge de escritorio) | ⚙️ Lectura lista y configurable; falta probar con la báscula del cliente |
| Báscula que imprime etiqueta | Etiqueta con peso o precio (nomenclatura de la tienda) | ✅ La caja VLUX la lee con el lector |

## Lector de códigos USB

Los lectores de pistola funcionan en **modo teclado**: escriben el código y un
Enter. No requieren configuración ni controladores, y conviven con el escáner
móvil. Las pruebas automáticas simulan exactamente esas pulsaciones
(`vlux_pos_catalog`: código conocido, desconocido y repetido), así que este
camino no se rompe sin que el CI lo note.

## Impresora de tickets

VLUX usa el soporte nativo de Odoo para impresoras **Epson con ePOS-Print**
(por ejemplo TM-m30III o TM-T88VII con interfaz de red). No hace falta caja IoT
ni instalar nada en la PC: el navegador de la caja habla directamente con la
impresora.

**Configuración**: Punto de venta → la caja → *Impresora ePos* → dirección IP de
la impresora. `vlux-cloud doctor <tienda>` reporta las impresoras configuradas y
**avisa (WARN)** si alguna quedó con la dirección de ejemplo `0.0.0.0`.

**El punto crítico: HTTPS.** La caja se sirve por HTTPS, y un navegador no deja
que una página segura llame a un equipo por HTTP. Dos formas de resolverlo:

1. **Recomendada:** activar en la impresora la opción *Automatic Certificate
   Update* de Epson y capturar en Odoo el **número de serie** en lugar de la IP.
   Odoo lo traduce a un dominio certificado (`omnilinkcert.epson.biz`) que el
   navegador acepta.
2. Instalar en la impresora un certificado propio que la PC de la caja confíe.

**Red**: la impresora necesita IP fija o reserva en el DHCP, y debe estar en la
misma red que la caja.

## Cajón de dinero

Se conecta a la impresora con cable RJ11/RJ12 en el puerto **DK**, y la
impresora lo abre al imprimir el ticket. No se conecta a la PC ni lleva
configuración aparte: si imprime, abre.

## Pantalla de cliente

Odoo POS puede mostrar el total en una segunda pantalla. Se abre desde la propia
caja y funciona con un monitor conectado a la PC o con una tablet apuntando a la
misma dirección.

## Datos fiscales del ticket

El ticket imprime el nombre, domicilio, teléfono y RFC de la empresa, más dos
campos VLUX (Ajustes → Empresas):

- **Régimen fiscal**: por ejemplo `601 - General de Ley Personas Morales`.
- **Leyenda del ticket**: por defecto `Este ticket no es un comprobante fiscal`.

Un tour automático hace una venta completa y verifica que los tres aparezcan
impresos. El timbrado de facturas (CFDI) **no** está incluido: `vlux_facturacion`
sigue en modo simulación.

## Báscula con cable (caja VLUX)

La caja VLUX (PWA) lee el peso de una báscula conectada por cable serial
(RS-232, con adaptador USB-serial si la PC no tiene puerto) usando Web Serial:
funciona en **Chrome o Edge de escritorio** por HTTPS (no en celulares ni en
Safari). En el diálogo de peso aparece **Conectar báscula** la primera vez
(el navegador pide elegir el puerto) y después **Leer báscula**, que además se
lee sola al abrir el diálogo. Escribir el peso a mano sigue funcionando.

El protocolo cambia entre marcas y modelos; el de la Torrey PCR-40T no está
publicado. Por eso los ajustes son de cada equipo (engrane en el diálogo):
velocidad (9600 por defecto), paridad y bits, y el **comando para pedir el
peso** (`P` por defecto; vacío si la báscula manda el peso sola). La caja toma
el último número de la línea que manda la báscula (con su unidad kg/g/lb) y
avisa si la báscula dice que el peso aún se mueve. "Lo último que mandó"
muestra el texto crudo para afinar los ajustes. La página permite el puerto
serie sólo para sí misma (`Permissions-Policy: serial=(self)`).

## Qué falta probar cuando llegue el equipo

1. Imprimir un ticket real y medir el tiempo de impresión.
2. Abrir el cajón desde la venta y en el corte de caja.
3. La ruta HTTPS con el certificado de la impresora, en la red de la tienda.
4. Un lector de pistola físico, además de la simulación por teclado.
5. La pantalla de cliente en un monitor real.
6. La báscula Torrey PCR-40T con su cable: velocidad, comando y formato del peso.
