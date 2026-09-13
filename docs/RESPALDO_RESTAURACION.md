# Respaldo y restauración

## Principio

Una base Odoo está compuesta por:

1. Dump PostgreSQL.
2. Filestore de la misma base y del mismo momento.
3. Commit exacto de Odoo core y release VLUX exacta instalada.
4. Inventario de configuración necesario para restaurar el servicio.

Un dump sin filestore puede dejar imágenes y adjuntos perdidos. Un filestore sin
dump no permite relacionar sus archivos.

## Política mínima

- Respaldo diario automático.
- Copia cifrada fuera del servidor.
- Retención diaria, semanal y mensual definida por negocio.
- Hash de integridad.
- Monitoreo del resultado.
- Restauración de prueba periódica en staging.
- RPO y RTO acordados por escrito.

## Respaldo coordinado en Windows

El siguiente procedimiento usa `data_dir = C:\Odoo\data`. Ajuste las rutas a la
configuración real.

### 1. Preparar destino

```powershell
$Database = "vlux_pos_prod"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Backup = "D:\OdooBackups\${Database}_${Timestamp}"
New-Item -ItemType Directory -Path $Backup
```

### 2. Detener escrituras

Active mantenimiento y detenga Odoo de forma limpia. Confirme que no hay un
proceso escribiendo en la base o filestore.

### 3. Crear dump

```powershell
& "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" `
  -h localhost `
  -p 5432 `
  -U odoo `
  -W `
  -d $Database `
  -Fc `
  --file "$Backup\$Database.dump"
```

`-W` solicita la contraseña sin escribirla en el comando ni en el script.

### 4. Copiar filestore

```powershell
Copy-Item `
  -LiteralPath "C:\Odoo\data\filestore\$Database" `
  -Destination "$Backup\filestore\$Database" `
  -Recurse
```

### 5. Registrar versiones

Registre el commit de Odoo core, la release VLUX activa y el manifest de release
si está disponible:

```powershell
git -C C:\Odoo\src\odoo rev-parse HEAD |
  Set-Content "$Backup\odoo-core-commit.txt"

Copy-Item C:\Odoo\vlux_current_release.txt `
  "$Backup\vlux-current-release.txt" `
  -ErrorAction SilentlyContinue

Copy-Item C:\Odoo\vlux_releases\CURRENT_VERSION\release-manifest.json `
  "$Backup\release-manifest.json" `
  -ErrorAction SilentlyContinue
```

Registre también versión de PostgreSQL, Python, nombre de base, fecha y motivo
del respaldo. No copie `odoo.conf` con secretos al respaldo sin cifrado y control
de acceso.

### 6. Validar

```powershell
& "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" `
  --list "$Backup\$Database.dump" `
  | Set-Content "$Backup\dump-catalog.txt"

Get-FileHash "$Backup\$Database.dump" -Algorithm SHA256 |
  Format-List |
  Set-Content "$Backup\dump-sha256.txt"
```

Compruebe que dump, catálogo, hash y filestore existen y no están vacíos. Inicie
Odoo nuevamente solo después de terminar la copia coordinada.

## Restauración en una base nueva

Pruebe siempre con un nombre distinto, por ejemplo `vlux_pos_restore_test`.

### 1. Preparar código

Prepare el baseline de Odoo registrado y active exactamente la release VLUX
registrada en el backup. No restaure primero y busque versiones después. En
clientes, use el paquete `VLUX_POS_<version>.zip` correspondiente, no `git pull`.

### 2. Crear base vacía

```powershell
& "C:\Program Files\PostgreSQL\16\bin\createdb.exe" `
  -h localhost `
  -p 5432 `
  -U odoo `
  -W `
  -T template0 `
  vlux_pos_restore_test
```

### 3. Restaurar dump

```powershell
& "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" `
  -h localhost `
  -p 5432 `
  -U odoo `
  -W `
  -d vlux_pos_restore_test `
  --no-owner `
  --no-privileges `
  "D:\OdooBackups\RESPALDO\vlux_pos_prod.dump"
```

### 4. Restaurar filestore

La carpeta del filestore debe tener exactamente el nombre de la nueva base:

```powershell
Copy-Item `
  -LiteralPath "D:\OdooBackups\RESPALDO\filestore\vlux_pos_prod" `
  -Destination "C:\Odoo\data\filestore\vlux_pos_restore_test" `
  -Recurse
```

El destino debe estar vacío antes de copiar. No mezcle archivos de respaldos
distintos.

### 5. Iniciar y validar

```powershell
& C:\Odoo\venv\Scripts\python.exe `
  C:\Odoo\src\odoo\odoo-bin `
  -c C:\Odoo\config\odoo.conf `
  -d vlux_pos_restore_test `
  --stop-after-init
```

Ajuste temporalmente `dbfilter` si impide abrir la base restaurada. Después
ejecute la lista de [Validación](VALIDACION.md).

## Sanitización para staging

Una restauración productiva contiene datos personales, usuarios, sesiones y
tokens. Antes de utilizarla en staging:

- Cambie o desactive correos salientes.
- Revoque API keys y tokens compartidos.
- Reemplace datos personales según la política aplicable.
- Cambie dominios y `web.base.url`.
- Cambie `vlux_mobile_scanner.base_url`.
- Desactive integraciones externas y trabajos peligrosos.
- Use contraseñas distintas.

## Prueba de restauración

Una copia no se considera respaldo verificado hasta demostrar que:

1. PostgreSQL restaura sin errores.
2. Odoo carga el registro de la base.
3. Los tres addons están disponibles.
4. Imágenes y adjuntos abren.
5. El POS inicia.
6. El dashboard consulta datos.
7. Se puede completar una prueba controlada.
8. Se documentan duración y resultado.

El catálogo de `pg_restore` y un hash solo prueban que el archivo es legible; no
sustituyen una restauración completa.

## Rollback de release

Si una migración de módulos ya corrió, el rollback debe restaurar de forma
consistente:

1. PostgreSQL desde el dump previo.
2. Filestore del mismo backup.
3. Release VLUX previa registrada.

No use DB nueva con código viejo ni código viejo con DB migrada. En Windows,
`scripts\windows\06_rollback.bat` requiere backup, release previa y confirmación
explícita `CONFIRM`.
