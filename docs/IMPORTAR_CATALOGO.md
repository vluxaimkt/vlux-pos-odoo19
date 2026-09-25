# Importar el catálogo desde Excel

Carga masiva de productos para una tienda nueva o para actualizar precios y
existencias de golpe. Vive en `vlux_pos_catalog` (versión 19.0.1.3.0).

## Quién puede

Administrador VLUX, Dueño (lo hereda del administrador) y Operador de
inventario. El cajero no ve el menú y el servidor rechaza la operación aunque
alguien la intente por otra vía. Cada empresa sólo ve sus importaciones.

## Paso a paso

1. **Punto de venta → Productos → Importar catálogo → Nuevo.**
2. **Descarga la plantilla** (enlace en la misma pantalla). Trae cuatro hojas:
   `Productos` (la que se llena), `Impuestos` (los nombres exactos de los
   impuestos de *tu* empresa), `Categorias` (las que ya existen) e
   `Instrucciones`.
3. Llena la hoja `Productos` y súbela (`.xlsx` o `.csv`).
4. **Validar archivo.** Lee todo y **no cambia nada del catálogo**. Muestra
   cuántos productos se van a crear, cuántos a actualizar, y la lista de
   errores con la fila y la columna de cada uno.
5. Corrige el archivo y vuelve a subirlo, o importa así: las filas con error se
   omiten, las válidas se aplican.
6. **Importar.** Corre en segundo plano; puedes cerrar la pantalla. **Actualizar
   avance** muestra cuántas filas van. Al terminar: creados, actualizados, sin
   cambios, existencias ajustadas y errores.

## Columnas

| Columna | Obligatoria | Notas |
| --- | --- | --- |
| `codigo_barras` | sí (o `referencia`) | Identifica el producto. Si ya existe en la empresa, la fila lo **actualiza**; si no, lo **crea**. |
| `nombre` | para productos nuevos | |
| `precio_venta` | para productos nuevos | Acepta `18.50`, `18,50`, `$1,234.50`, `1.234,50`. |
| `costo` | no | |
| `referencia` | no | Identifica el producto cuando no tiene código de barras (granel). |
| `categoria_pos` | no | El botón del punto de venta. Subcategorías con `/`: `Abarrotes / Galletas`. |
| `categoria` | no | Categoría interna. |
| `impuestos` | no | Nombre de la hoja `Impuestos`. Vacío = el impuesto por defecto de la empresa (en productos nuevos). Varios: `IEPS 8% + IVA 16%`. Sin impuesto: `ninguno`. También acepta el porcentaje (`16`) si sólo hay un impuesto con ese valor. |
| `existencia` | no | Cantidad en inventario en la ubicación elegida. **Reemplaza** la existencia, no suma. |
| `inventariable` | no | `si`/`no`. Por defecto sí cuando hay existencia. |
| `disponible_pos` | no | `si`/`no`. Por defecto sí. |

Los encabezados se reconocen en español o inglés, con o sin acentos
(`Código de barras`, `barcode`, `Precio`, `IVA`, `Stock`…). Columnas que no
se reconocen se ignoran.

**Una celda vacía deja ese dato como está** al actualizar. Por eso un archivo
con sólo `codigo_barras` y `precio_venta` es una actualización de precios.

## Qué se garantiza

- **Validar no escribe nada.**
- **Importar dos veces el mismo archivo no duplica** ni reescribe: cada fila
  busca el producto por código de barras (o referencia) antes de crearlo; lo
  que no cambió cuenta como *sin cambios*, no se toca y no genera
  movimientos de inventario ni obliga a las cajas a volver a sincronizarlo.
- **Una fila mala no tumba a las demás**: si un lote falla, se reintenta fila
  por fila y sólo la culpable queda como error.
- **Si el servidor se reinicia a medio proceso**, el trabajo sigue donde se
  quedó (se confirma cada 500 filas y las filas son idempotentes).
- Un código de barras que pertenece a **otra empresa** se reporta y no se toca.
- Un producto **archivado** que viene en el archivo se reactiva.
- Las existencias se ajustan con el ajuste de inventario estándar de Odoo
  (movimiento y valoración), igual que si se capturaran a mano.

Límites: 50 000 filas y 20 MB por archivo.

## Rendimiento medido

`tools/perf/bench_catalog_import.py`, base sintética de 10 000 productos, host
de desarrollo:

| Caso | Tiempo |
| --- | --- |
| Validar 10 000 filas | 6 s |
| Carga inicial: 5 000 altas + 5 000 actualizaciones + ~10 000 ajustes de existencia | 11 min (679 s) en segundo plano |
| Reimportar el mismo archivo (todo sin cambios) | 19 s, 0 escrituras, 0 movimientos |
| Actualizar el precio de 10 000 productos | 15 s |

Casi todo el tiempo de la carga inicial son los ajustes de inventario: Odoo
genera un movimiento con su valoración por producto. Es lo mismo que haría la
importación estándar de Odoo; no se salta la contabilidad.
