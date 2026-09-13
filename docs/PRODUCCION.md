# Producción controlada

## Estado actual

El código de los tres addons superó instalación limpia, migración desde respaldo,
pruebas automatizadas y pruebas HTTP de autorización. Las restricciones usan la
API de Odoo 19; los tokens públicos tienen expiración, revocación y hash; y las
rutas sensibles aplican límites de peticiones.

Esto no autoriza todavía un despliegue. Los requisitos externos pendientes son:

1. Aprovisionar staging y producción detrás de HTTPS y proxy inverso.
2. Definir dominio, certificados, `dbfilter`, servicio y monitoreo reales.
3. Rotar las credenciales que hayan aparecido en archivos o consolas locales.
4. Probar restauración y rollback en staging con la release candidata.
5. Completar la validación funcional con un POS y un teléfono reales.
6. Aprobar explícitamente que `vlux_facturacion` opere solo como simulador.

Si el objetivo es emitir CFDI, `vlux_facturacion` sigue bloqueado: no implementa
PAC productivo, CSD, timbrado, cancelación ni comunicación con el SAT.

## Topología recomendada

```text
Internet
   |
HTTPS 443
   |
Nginx / proxy inverso
   |-- HTTP Odoo 127.0.0.1:8069
   `-- WebSocket 127.0.0.1:8072
             |
           Odoo 19
             |
        PostgreSQL 16
             |
   data_dir y respaldos cifrados
```

Use Linux con `systemd` o contenedores supervisados. No exponga Werkzeug ni
PostgreSQL directamente a Internet.

## Ambientes

Mantenga tres bases y secretos separados:

```text
vlux_pos_dev
vlux_pos_stage
vlux_pos_prod
```

Staging debe usar una copia sanitizada o datos sintéticos. Nunca copie tokens,
contraseñas o datos personales productivos sin un proceso autorizado.

## Control de versiones

Cada despliegue debe identificar:

- Paquete `VLUX_POS_<version>.zip` aprobado.
- `release-manifest.json` asociado.
- SHA256 del paquete verificado.
- Commit fuente interno del repositorio VLUX POS.
- Commit exacto de Odoo core.
- Versiones de Python, PostgreSQL y dependencias.
- Lista y versión de addons instalados.
- Respaldo previo asociado.

Flujo recomendado:

1. Desarrollar en una rama.
2. Revisar cambios mediante pull request.
3. Ejecutar instalación limpia y pruebas.
4. Desplegar el commit en staging.
5. Ejecutar validación funcional.
6. Construir `VLUX_POS_<version>.zip` con `scripts\release\BUILD_RELEASE.bat`.
7. Verificar manifest y SHA256.
8. Respaldar producción.
9. Desplegar exactamente el paquete aprobado.
10. Ejecutar migración dirigida y health checks.

GitHub privado es fuente interna de VLUX. Los clientes no deben ejecutar
`git pull`, `git fetch` ni recibir acceso al historial Git.

## Configuración mínima de Odoo

Ejemplo orientativo para una instancia detrás de proxy:

```ini
[options]
admin_passwd = SECRETO_MAESTRO_ALEATORIO
db_host = 127.0.0.1
db_port = 5432
db_user = odoo_prod
db_password = SECRETO_POSTGRESQL_ALEATORIO
dbfilter = ^vlux_pos_prod$
list_db = False

addons_path = /opt/odoo/core/odoo/addons,/opt/odoo/core/addons,/opt/odoo/vlux-pos
data_dir = /var/lib/odoo
logfile = /var/log/odoo/odoo.log

http_interface = 127.0.0.1
http_port = 8069
gevent_port = 8072
proxy_mode = True

workers = 3
max_cron_threads = 1
limit_time_cpu = 120
limit_time_real = 240
```

Calcule `workers` según CPU, memoria y carga real. Pruebe el valor en staging.
Proteja el archivo con permisos exclusivos del usuario del servicio.

## Servicio systemd de referencia

```ini
[Unit]
Description=Odoo 19 VLUX POS
After=network.target postgresql.service

[Service]
Type=simple
User=odoo
Group=odoo
ExecStart=/opt/odoo/venv/bin/python /opt/odoo/core/odoo-bin -c /etc/odoo/odoo.conf
Restart=on-failure
RestartSec=5
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
```

El usuario `odoo` no debe ser administrador ni tener shell interactivo si no es
necesario. Debe tener acceso únicamente al código, configuración, logs y
`data_dir` requeridos.

## Proxy HTTPS de referencia

Fragmento conceptual de Nginx:

```nginx
upstream odoo_http {
    server 127.0.0.1:8069;
}

upstream odoo_websocket {
    server 127.0.0.1:8072;
}

server {
    listen 443 ssl http2;
    server_name pos.example.com;

    location /websocket {
        proxy_pass http://odoo_websocket;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location / {
        proxy_pass http://odoo_http;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 720s;
    }
}
```

Complete TLS, redirección HTTP, límites de tamaño, rate limiting y encabezados
según la infraestructura. Pruebe específicamente `/websocket` y
`/vlux/mobile/*`.

Los enlaces `/vlux/facturacion/access/*` contienen un token de un solo uso. No
registre esas rutas completas en el access log. Use una ubicación específica con
`access_log off` o una política de redacción equivalente. No registre tampoco
encabezados `Authorization`, cookies ni cuerpos JSON.

## Secretos

No almacene en Git:

- Contraseña maestra de Odoo.
- Contraseña PostgreSQL.
- API keys de Odoo.
- Certificados, llaves privadas o credenciales fiscales.
- Dumps, filestore o logs.

Defina responsable, ubicación, permisos, fecha de rotación y procedimiento de
revocación para cada secreto.

## Despliegue

En Windows Local use el paquete aprobado y los scripts de
`scripts\windows`:

```bat
set ODOO_DB=vlux_pos_prod
set ODOO_SERVICE_NAME=odoo-vlux
set BACKUP_ROOT=D:\OdooBackups
set VLUX_PROFILE=scanner_owner
VLUX_UPDATE.bat C:\Updates\VLUX_POS_1.0.1.zip
```

El perfil decide qué módulos VLUX se actualizan. No use `-u all` y no instale
`vlux_facturacion` automáticamente en todos los clientes; sigue siendo
simulador.

Después verifique login, POS, WebSocket, escáner, dashboard, logs y el estado de
los addons contratados.

## Rollback

Un rollback de Odoo no consiste únicamente en regresar archivos:

1. Detenga tráfico y Odoo.
2. Restaure el dump anterior.
3. Restaure el filestore de ese mismo respaldo.
4. Regrese código VLUX y Odoo a sus commits registrados.
5. Inicie Odoo y ejecute validaciones.

No use una base migrada con código antiguo sin una migración inversa comprobada.

## Observabilidad

Producción debe contar con:

- Health check HTTP externo.
- Monitoreo de proceso, CPU, memoria y disco.
- Monitoreo de PostgreSQL y conexiones.
- Rotación y retención de logs.
- Alertas de errores HTTP y trabajos cron.
- Métricas de espacio del filestore y respaldos.
- Alerta si el último respaldo o prueba de restauración vence.

## Criterio de autorización

Producción se autoriza únicamente cuando la lista de
[Validación](VALIDACION.md) está completa, los bloqueadores de este documento
están resueltos y existe una restauración de staging exitosa con la misma
release.
