# Operar sin internet

Qué pasa cuando la tienda se queda sin conexión, qué sigue funcionando y qué
no. Aplica al POS de Odoo 19 con los módulos VLUX. Lo marcado **probado** lo
cubre una prueba automática; lo marcado *sin probar* está deducido del código
y falta verificarlo con equipo físico.

## Dos formas de instalar, dos respuestas

| Instalación | Si se cae el internet de la tienda |
| --- | --- |
| **Local (Windows, servidor dentro de la tienda)** | El POS habla con un servidor que está en la misma red: **todo sigue funcionando**, incluido abrir y cerrar caja, dar de alta productos y el escáner con celular (por Wi-Fi local). Sólo se detienen lo que vive afuera: terminal bancaria, facturación CFDI, respaldos a R2, panel del dueño fuera de la tienda y actualizaciones. |
| **Nube** | El POS pierde su servidor. Sigue vendiendo con lo que ya cargó (abajo) y sincroniza al volver. |

Para una tienda con internet inestable, la instalación local es la opción
robusta. Para las demás, la nube con el modo sin internet de Odoo es
suficiente para cortes cortos.

## Nube: qué sigue funcionando sin internet

Requisito: **la caja se abrió con internet** (la apertura necesita al servidor)
y la pantalla del POS ya estaba cargada.

| Acción | Sin internet |
| --- | --- |
| Vender productos ya cargados en la caja, con lector USB o tocando la pantalla | **Sí — probado** |
| Cobrar en efectivo | **Sí — probado** |
| Cobrar con tarjeta capturando el monto (sin terminal integrada) | Sí (mismo flujo que efectivo) |
| Imprimir el ticket en impresora de red de la tienda | *Sin probar* (la impresora está en la red local; depende de la ruta HTTPS elegida, ver `HARDWARE.md`) |
| Recargar la página del POS | **Sí — probado** ("Continuar con funcionalidad limitada") |
| Las ventas llegan al servidor al volver el internet, cada una una sola vez | **Sí — probado** |
| Código de un producto que la caja no tiene cargado | **No**: aviso *"Sin conexión: el código … no está en esta caja. Podrás registrarlo cuando vuelva el internet."* — **probado** |
| Alta rápida de un producto nuevo | **No** (el formulario ni se abre); si la conexión se pierde con el formulario abierto: *"Sin conexión: no se guardó nada."* |
| Escáner con celular | **No** en nube: el celular también necesita llegar al servidor. Guarda las lecturas en su cola y las entrega al volver la red, sin duplicar (ver `OPERABILIDAD_PERFORMANCE.md`) |
| Terminal bancaria integrada | No (necesita internet) |
| Cerrar la caja (corte) | **No**: se hace al volver la conexión |
| Facturar | No |
| Panel del dueño | Muestra los datos hasta el último momento con conexión |

Las ventas hechas sin internet se guardan **en el navegador** de esa caja
(IndexedDB) hasta sincronizarse. Mientras haya ventas pendientes:

- **No borrar los datos del navegador** ni usar modo incógnito.
- No cerrar la caja desde otro equipo.
- El indicador de conexión arriba a la derecha muestra que hay pendientes;
  cuando desaparece, todo llegó al servidor.

## El límite de 5 000 productos

Al abrir la caja, Odoo carga **hasta 5 000 productos** (favoritos, con
movimiento reciente y modificados recientemente) y busca los demás en el
servidor cuando se escanean. **Sin internet, un producto que no se cargó no
se puede vender.**

- Tienda con menos de 5 000 productos: no hay nada que hacer, se cargan todos.
- Tienda con más y con internet inestable: subir el límite en *Ajustes →
  Técnico → Parámetros del sistema → `point_of_sale.limited_product_count`*
  al tamaño del catálogo (o `0` para cargar todo). Abrir la caja tarda más la
  primera vez; las reaperturas sólo traen lo que cambió. Es una decisión por
  tienda (M5 del seguimiento).

## Qué se prueba

`vlux_pos_catalog/tests/test_tours.py::test_offline_day_syncs_every_sale_once`
(navegador, job `e2e`): se abre la caja, se corta la red, se venden 2
unidades con el lector, se escanea un código desconocido (aviso claro, sin
formulario), se recarga la página sin red, se vende otra unidad, vuelve la
red y el servidor recibe exactamente las 2 ventas, sin duplicados y sin crear
nada por el código desconocido.

## Pendiente con equipo físico

- Impresión de ticket y apertura de cajón sin internet (M15).
- Un corte de red real de 30 minutos en la tienda piloto con el celular y el
  lector USB.
