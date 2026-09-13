# Instalación en Windows

Esta guía prepara una instalación nueva de Odoo 19 Community con los addons
VLUX POS. Los comandos usan rutas de ejemplo bajo `C:\Odoo`.

## 1. Requisitos

Instale antes de continuar:

- Git para Windows.
- Python 3.12 de 64 bits.
- PostgreSQL 16 con herramientas de línea de comandos.
- wkhtmltopdf 0.12.6 con Qt parcheado si se generarán PDF.
- Microsoft C++ Build Tools si alguna dependencia Python necesita compilarse.

Compruebe las herramientas:

```powershell
git --version
py -3.12 --version
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" --version
& "C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe" --version
```

## 2. Crear estructura local

```powershell
New-Item -ItemType Directory -Force C:\Odoo\src
New-Item -ItemType Directory -Force C:\Odoo\custom_addons
New-Item -ItemType Directory -Force C:\Odoo\config
New-Item -ItemType Directory -Force C:\Odoo\data
New-Item -ItemType Directory -Force C:\Odoo\logs
```

## 3. Instalar Odoo core

```powershell
git clone --branch 19.0 https://github.com/odoo/odoo.git C:\Odoo\src\odoo
git -C C:\Odoo\src\odoo checkout a2d73c5900d8886d115afe1ccb7f5c97c7e71a97
```

Verifique el commit:

```powershell
git -C C:\Odoo\src\odoo rev-parse HEAD
```

Debe mostrar:

```text
a2d73c5900d8886d115afe1ccb7f5c97c7e71a97
```

## 4. Clonar VLUX POS

```powershell
git clone https://github.com/vluxaimkt/vlux-pos-odoo19.git `
  C:\Odoo\custom_addons\vlux-pos-odoo19
```

La raíz clonada contiene directamente las carpetas de los addons y debe
agregarse completa a `addons_path`.

## 5. Crear entorno Python

```powershell
py -3.12 -m venv C:\Odoo\venv
& C:\Odoo\venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
& C:\Odoo\venv\Scripts\python.exe -m pip install `
  -r C:\Odoo\src\odoo\requirements.txt
& C:\Odoo\venv\Scripts\python.exe -m pip install `
  -r C:\Odoo\custom_addons\vlux-pos-odoo19\requirements-extra.txt
```

Verifique el entorno:

```powershell
& C:\Odoo\venv\Scripts\python.exe -m pip check
& C:\Odoo\venv\Scripts\python.exe -m pip show qrcode
```

## 6. Preparar PostgreSQL

Abra `psql` como administrador de PostgreSQL:

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -d postgres -W
```

Ejecute SQL sustituyendo el marcador por una contraseña local fuerte:

```sql
CREATE ROLE odoo LOGIN PASSWORD 'REEMPLAZAR_CON_SECRETO_LOCAL';
ALTER ROLE odoo CREATEDB;
CREATE DATABASE vlux_pos_dev
  OWNER odoo
  ENCODING 'UTF8'
  TEMPLATE template0;
\q
```

No reutilice esta contraseña en staging o producción.

## 7. Crear configuración local

```powershell
Copy-Item `
  C:\Odoo\custom_addons\vlux-pos-odoo19\config\odoo.conf.example `
  C:\Odoo\config\odoo.conf
```

Edite únicamente la copia `C:\Odoo\config\odoo.conf` y cambie:

- `admin_passwd` por un secreto aleatorio.
- `db_password` por la contraseña del rol `odoo`.
- `addons_path` si utilizó otras rutas.
- `data_dir` y `logfile` si corresponde.

No agregue la copia con secretos al repositorio.

## 8. Inicializar los addons

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  -c C:\Odoo\config\odoo.conf `
  -d vlux_pos_dev `
  -i vlux_mobile_scanner,vlux_owner,vlux_facturacion `
  --without-demo `
  --stop-after-init
```

Odoo instalará automáticamente las dependencias estándar declaradas en los
manifiestos. Revise `C:\Odoo\logs\odoo.log` si el comando termina con error.

## 9. Iniciar Odoo

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  -c C:\Odoo\config\odoo.conf `
  -d vlux_pos_dev
```

Mantenga la consola abierta durante el desarrollo. Para detenerlo use
`Ctrl+C` y espere el cierre del proceso.

Abra:

```text
http://127.0.0.1:8069/web?db=vlux_pos_dev
```

## 10. Verificación inicial

Confirme lo siguiente:

1. Odoo responde con HTTP 200.
2. Aplicaciones muestra los tres addons como instalados.
3. Puede crear una configuración y abrir una sesión POS.
4. El botón **Escáner móvil** aparece en la pantalla de productos.
5. Un usuario con grupo `VLUX Owner` abre `/vlux-owner/`.
6. `VLUX Facturación` muestra el proveedor simulador y permanece en simulación.

Continúe con [Configuración funcional](CONFIGURACION_FUNCIONAL.md) y
[Validación](VALIDACION.md).

## Actualizar una instalación local

GitHub privado es fuente interna de VLUX, no un mecanismo de actualización para
clientes. Un equipo de cliente no debe tener token GitHub, deploy key, historial
Git ni ejecutar `git pull` o `git fetch`.

VLUX debe entregar un paquete aprobado:

```text
VLUX_POS_<version>.zip
release-manifest.json
VLUX_POS_<version>.zip.sha256
```

El operador ejecuta el actualizador Windows desde `scripts\windows`:

```bat
set ODOO_DB=vlux_pos_dev
set VLUX_PROFILE=scanner_owner
VLUX_UPDATE.bat C:\Updates\VLUX_POS_1.0.1.zip
```

El actualizador ejecuta preflight, respaldo obligatorio de PostgreSQL y
filestore, staging, verificación SHA256/manifest, activación, actualización
dirigida de módulos y smoke test. Nunca usa `-u all`; `vlux_facturacion` sólo se
actualiza con el perfil explícito `facturacion_internal` o `custom`.

Si falla una etapa, el flujo se detiene y conserva logs/backup. El rollback es
manual y explícito con `06_rollback.bat`, restaurando siempre DB + filestore +
release previa.
