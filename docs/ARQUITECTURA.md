# Arquitectura de VLUX POS

## Objetivo

VLUX POS extiende el módulo estándar `point_of_sale` de Odoo 19. Los addons de
este repositorio no reemplazan Odoo ni implementan un servidor de base de datos.

```text
Navegador POS / Teléfono / Dashboard
                  |
               HTTP(S)
                  |
                Odoo 19
        +---------+---------+
        |         |         |
     Escáner    Owner   Facturación
        |         |         |
        +---------+---------+
                  |
              PostgreSQL
                  |
              Filestore
```

## Componentes

### Odoo core

Proporciona autenticación, ORM, interfaz web, POS, inventario, contabilidad,
localización mexicana y comunicación en tiempo real. Se instala desde el
repositorio oficial de Odoo y no se duplica en este repositorio.

### `vlux_mobile_scanner`

Agrega al POS el botón **Escáner móvil**. La caja genera un código temporal y
una URL para vincular un teléfono. El teléfono envía códigos de barras y el POS
los procesa mediante el flujo estándar de Odoo.

Datos principales:

- `vlux.mobile.scanner.pairing`: vínculo entre teléfono, caja y sesión POS.
- `vlux.mobile.scanner.event`: historial y resultado de cada lectura.
- Tokens móviles almacenados como hash y con vigencia de ocho horas.
- Código de emparejamiento de ocho caracteres con vigencia de diez minutos.
- Límites de peticiones persistentes y retención automática de treinta días.

El addon usa rutas públicas para el teléfono, autenticadas por token después del
emparejamiento. En producción deben estar detrás de HTTPS.

### `vlux_owner`

Expone una PWA para propietarios en `/vlux-owner/`. Calcula métricas desde
`pos.order`, `pos.order.line`, `pos.config` y `product.product`.

El acceso normal requiere sesión Odoo y pertenecer al grupo `VLUX Owner`. El
endpoint compartido usa autenticación Bearer con API Keys nativas de Odoo,
comprueba el mismo grupo y limita peticiones por usuario.

### `vlux_facturacion`

Extiende el portal del ticket POS para capturar datos fiscales y generar un XML
de simulación. Crea estos modelos:

- `vlux.fiscal.config`
- `vlux.fiscal.request`
- `vlux.pac.provider`

El proveedor incluido es `VLUX PAC Simulador`. No genera CFDI válido, UUID SAT,
timbrado, cancelación ni consulta ante un PAC.

El enlace de resultado usa un token aleatorio almacenado como hash, válido por
diez minutos y canjeable una sola vez. El navegador recibe después una cookie
`HttpOnly` de una hora para consultar el resultado y descargar el XML.

## Selección de base de datos

Los addons no se conectan directamente a una base con nombre fijo. Odoo recibe
la conexión PostgreSQL desde `odoo.conf` y selecciona la base con `-d` o con el
filtro configurado:

```powershell
python odoo-bin -c C:\Odoo\config\odoo.conf -d vlux_pos_dev
```

Al instalar un addon, Odoo crea sus tablas y registros en esa base. Los mismos
archivos pueden instalarse en `vlux_pos_dev`, `vlux_pos_stage` y
`vlux_pos_prod`, pero los datos y parámetros de cada ambiente son independientes.

## Estado que no viaja en Git

Estos elementos viven en PostgreSQL:

- Usuarios, grupos y compañías.
- Productos, códigos de barras, precios e inventario.
- Configuraciones, sesiones y ventas POS.
- Parámetros `ir.config_parameter`.
- Solicitudes fiscales, tokens y eventos del escáner.

Estos elementos viven en el filestore:

- Imágenes de productos y compañías.
- Adjuntos.
- XML generados.
- Documentos cargados desde Odoo.

Un despliegue exacto requiere mantener alineados código, base y filestore.

## Perfiles de ambiente

| Ambiente | Base sugerida | Datos | Exposición |
| --- | --- | --- | --- |
| Desarrollo | `vlux_pos_dev` | Ficticios o locales | Solo equipo/red de desarrollo |
| Staging | `vlux_pos_stage` | Copia sanitizada | Acceso restringido |
| Producción | `vlux_pos_prod` | Reales | HTTPS y controles operativos |

No reutilice credenciales, tokens ni filestores entre ambientes.
