# Alta de una tienda nueva (primer cliente)

Guía para dejar una tienda vendiendo en la nube VLUX con el POS de Odoo.
Tiempo estimado: 1–2 horas si el catálogo ya viene en Excel.

## 0. Datos que hay que tener ANTES

| Dato | Ejemplo | Para qué |
|---|---|---|
| Nombre corto del tenant | `abarrotes-lupita` | nombre interno y de la base |
| Subdominio | `lupita.vlux…` | la dirección que abre la tienda |
| Correo del dueño | `dueno@…` | usuario Owner |
| Razón social, RFC, régimen fiscal | `626 - Régimen Simplificado de Confianza` | encabezado del ticket |
| Dirección, CP, estado, teléfono, correo | | ticket |
| Leyenda del ticket | `Gracias por su compra` | ticket |
| ¿Sus precios de etiqueta ya traen IVA? | casi siempre **sí** | ver paso 3 (no se puede cambiar después de la primera venta) |
| Cajeros y su PIN (4 dígitos) | | entrar a la caja |
| Catálogo en Excel | código de barras, nombre, precio, IVA, existencia | [IMPORTAR_CATALOGO.md](IMPORTAR_CATALOGO.md) |
| Modelo de impresora de tickets | | paso 7 |

Los PIN y el JSON de la tienda **no se suben al repositorio ni se mandan por chat**.

## 1. Crear el tenant

```bash
vlux-cloud provision <tenant> --domain <subdominio> --owner-email <correo> \
  --country MX --data-class REAL_CLIENT_DATA --profile small --company-name "<Razón social>"
```

## 2. Publicar el subdominio (lo hace una persona en Cloudflare)

Zero Trust → Networks → Tunnels → el túnel de VLUX → *Public Hostnames* →
*Add*: el subdominio, servicio **HTTP** `vlux-edge-caddy:80`, sin cambiar el
encabezado Host. Después: `vlux-cloud doctor <tenant>` debe salir OK.

## 3. Configurar la tienda en un paso

Llenar un `store.json` (formato en la cabecera de
`tools/onboarding/configure_store.py`) y ejecutar:

```bash
python tools/onboarding/configure_store.py store.json \
  | docker exec -i vlux-<tenant>-app /opt/vlux/pos/venv/bin/python \
    /opt/vlux/pos/odoo/odoo-bin shell -c /etc/vlux-pos/odoo.conf -d <base> --no-http
```

Debe terminar con `VLUX_STORE_READY` y la lista de lo que cambió. Deja:
español de México y zona horaria; datos fiscales del ticket; **precios con IVA
incluido** (`"prices_include_tax": false` si la tienda maneja precios sin
IVA); una caja con Efectivo y Tarjeta, entrada por empleado con PIN, límite de
diferencia en el corte; los cajeros. Se puede correr otra vez: sólo cambia lo
que sea distinto.

**El IVA incluido sólo se puede cambiar antes de la primera venta.** Si se
olvida, un producto de $20 se cobra $23.20.

## 4. Respaldos y monitoreo

```bash
vlux-cloud schedule install --tenant <tenant>
```

En UptimeRobot: monitor HTTP a `https://<subdominio>/vlux/ready`.

## 5. Catálogo

Descargar la plantilla desde *Punto de venta → Productos → Importar catálogo*, llenarla
(o adaptar el Excel del cliente), **Validar**, revisar errores, **Importar**.
Ver [IMPORTAR_CATALOGO.md](IMPORTAR_CATALOGO.md).

## 6. Prueba antes de entregar

1. Abrir la caja con el PIN de un cajero, fondo inicial.
2. Vender un producto con efectivo y otro con tarjeta; revisar que el total
   sea el precio de etiqueta y que el ticket muestre RFC, régimen y leyenda.
3. Escanear un código que no existe (alta rápida si el usuario tiene permiso).
4. Hacer el corte contando el efectivo ([CORTE_DE_CAJA.md](CORTE_DE_CAJA.md)).
5. Borrar nada: esas ventas de prueba quedan; si molestan, hacer la
   devolución desde el POS.

## 7. Impresora de tickets

Cuando se sepa el modelo: si es térmica USB/red de 80 mm (Epson TM-T20 o
compatible ESC/POS) se imprime desde el navegador con el diálogo de impresión;
ajustar tamaño de papel y quitar márgenes una vez en el equipo de la caja.

## 8. Guía del cajero

Imprimir [guia_cajero.html](guia_cajero.html) (2 hojas carta, instrucciones de
PDF en el propio archivo), llenar al pie encargado, teléfono y página de la
caja, y dejarla junto a la caja.

## 9. Modo sin internet

Explicar al cajero [MODO_SIN_INTERNET.md](MODO_SIN_INTERNET.md): puede seguir
vendiendo con los productos cargados; no debe cerrar la pestaña ni borrar datos
del navegador hasta que vuelva la conexión.
