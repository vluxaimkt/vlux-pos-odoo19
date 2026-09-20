# Distribucion de Produccion VLUX POS

## Principio

VLUX POS se distribuye como un producto con un release logico comun. Windows,
Ubuntu y Cloud no son forks: consumen el mismo artefacto canonico y el mismo
manifest.

```text
VLUX RELEASE
  -> Odoo 19 pinned
  -> vlux_core
  -> vlux_mobile_scanner
  -> vlux_owner
  -> vlux_pos_catalog
  -> release-manifest.json
  -> Windows Setup / Ubuntu deb / Cloud OCI
```

Baseline fijo:

| Componente | Version |
| --- | --- |
| Odoo Community | `a2d73c5900d8886d115afe1ccb7f5c97c7e71a97` |
| Python | `3.12.10` |
| PostgreSQL | `16.x` compatible, baseline `16.14` |
| VLUX Core | `19.0.1.2.0` |

`vlux_facturacion` se mantiene para regresion CI en modo simulacion. No forma
parte del perfil productivo por defecto y no habilita CFDI real.

## Artefacto Canonico

El generador principal es:

```sh
python scripts/release/build_release.py <version> --edition local_complete
```

Ediciones soportadas:

| Edicion | Addons incluidos |
| --- | --- |
| `local_core` | `vlux_core` |
| `local_complete` | `vlux_core`, `vlux_mobile_scanner`, `vlux_owner`, `vlux_pos_catalog` |
| `cloud_managed` | `vlux_core`, `vlux_mobile_scanner`, `vlux_owner`, `vlux_pos_catalog` |

Campos obligatorios del manifest:

- `product`
- `version`
- `build_date`
- `source_commit`
- `odoo_commit`
- `python_version`
- `postgresql_version`
- `edition`
- `included_addons`
- `addon_versions`
- `artifact_sha256`

## Build Multitarget

```sh
python scripts/release/build_distribution.py <version> --out-dir dist
```

Salida esperada:

```text
dist/
  common/
    VLUX_POS_local_complete_<version>.zip
    release-manifest.json
    SHA256SUMS
  windows/
    source/
    manifest.json
    SHA256SUMS
  ubuntu/
    source/
    manifest.json
    SHA256SUMS
  cloud/
    source/
    manifest.json
    SHA256SUMS
  manifest.json
```

Los binarios pesados (`.exe`, `.msi`, `.deb`, imagen OCI) se publican como
artifacts o releases del pipeline, no se versionan en Git.

## Windows

Objetivo:

- `VLUX_POS_Setup_<version>.exe`
- MSI interno `VLUX_POS_<version>_x64.msi`
- WiX Toolset v4 Burn bootstrapper

Layout:

- `C:\Program Files\VLUX\POS`
- `C:\ProgramData\VLUX\POS\config`
- `C:\ProgramData\VLUX\POS\data`
- `C:\ProgramData\VLUX\POS\filestore`
- `C:\ProgramData\VLUX\POS\backups`
- `C:\ProgramData\VLUX\POS\logs`

Uninstall preserva datos por defecto. El borrado de datos requiere flujo
separado y explicito.

Firma:

- `WINDOWS_SIGNING=PENDING`
- `GENERAL_AVAILABILITY_SIGNING_REQUIRED=YES`

## Ubuntu

Objetivo:

```sh
sudo apt install ./vlux-pos_<version>_amd64.deb
sudo vlux-pos setup --business-name "Cliente" --edition local_complete --hostname pos.example.com
```

Layout:

- `/opt/vlux/pos/`
- `/etc/vlux-pos/`
- `/var/lib/vlux-pos/`
- `/var/lib/vlux-pos/filestore/`
- `/var/backups/vlux-pos/`
- `/var/log/vlux-pos/`

Servicio:

- `vlux-pos.service`
- usuario dedicado `vlux-pos`
- PostgreSQL 16 privado
- Caddy para HTTPS

## Cloud Managed

El cliente no instala Cloud. VLUX opera cada host Ubuntu 24.04 con un unico
VLUX EDGE (Caddy) que publica 80/443 y un stack aislado por tenant:

- contenedor Odoo independiente
- contenedor PostgreSQL 16 independiente
- database y rol independientes
- filestore independiente
- secrets independientes
- logs y backups independientes
- red privada `vlux-<tenant>-private` independiente

PostgreSQL no va dentro del contenedor de aplicacion, no publica ningun puerto
del host y no es alcanzable desde el edge ni desde otro tenant. La unica
exposicion publica del host es el edge en 80/443.

Operacion (como root en el host):

```sh
sudo vlux-cloud host-init --acme-email ops@vlux.example
sudo vlux-cloud provision cliente01   --domain pos.cliente.com   --owner-email owner@cliente.com   --edition cloud_managed   --tls-mode public   --image ghcr.io/vluxaimkt/vlux-pos@sha256:<digest>
```

El detalle operativo completo (backup, off-site S3, restore, upgrade, disable,
entrega y borrado de la credencial inicial del Owner) esta en
`packaging/cloud/README.md`.

## Scanner

Flujo obligatorio en los tres targets:

```text
PHONE -> HTTPS -> VLUX Mobile Scanner -> bus -> POS
```

`curl` solo valida endpoints HTTP. Camara, permisos del navegador movil,
reconexion y escaneo fisico requieren prueba manual.

## Matriz De Validacion

| TARGET | CLEAN_INSTALL | REBOOT | SERVICE | POS | SALE | PAYMENT | STOCK | SCANNER_HTTP | PHONE_CAMERA | PHONE_REAL_SCAN | OWNER | ROLES | BACKUP | RESTORE | UPDATE | UNINSTALL_OR_DESTROY | DATA_PRESERVATION | HTTPS | REMOTE_CI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| WINDOWS_LOCAL | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | PASS | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | PASS |
| UBUNTU_LOCAL | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | PASS | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | PASS |
| CLOUD_MANAGED | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | PASS | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | MANUAL_PENDING | PASS |

No marcar pruebas fisicas como `PASS` hasta ejecutarlas en el ambiente real.
