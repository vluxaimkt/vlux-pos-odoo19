# VLUX POS Cloud Managed

Cloud is operated by VLUX on its own Ubuntu 24.04 LTS hosts. It is **not** a
customer installer. One host serves many tenants with hard isolation between
them; customers never run `vlux-cloud`.

Lab version: `0.0.0-cloud1`. Not production-approved - see
[Manual gates](#manual-gates-before-production).

## Architecture

```
                     Internet
                        |
                  80 / 443 (only public ports on the host)
                        |
              +---------v----------+
              |  VLUX EDGE (Caddy) |   one per host
              +----+----------+----+
                   |          |            network: vlux-edge
        cliente-a-app|        |cliente-b-app
        +----------v-+      +-v----------+
        |  Odoo A     |     |  Odoo B     |
        +------+------+     +------+------+
               |                   |        networks: vlux-<tenant>-private
        +------v------+     +------v------+   (internal: no route off-host)
        | PostgreSQL A|     | PostgreSQL B|
        +-------------+     +-------------+
```

* The edge is the only component that binds a host port. Tenant stacks publish
  nothing: not 5432, not 8069, not 8072.
* Each tenant app joins two networks - its own private network (to reach its
  PostgreSQL) and `vlux-edge` under the unique alias `<tenant>-app`. The unique
  alias is what lets many tenants share one edge network without colliding.
* Each tenant PostgreSQL joins **only** the tenant private network, which is
  declared `internal: true`. The edge cannot reach it, and neither can any
  other tenant.

### Host layout

```
/srv/vlux-pos/
    host.json                     host state written by host-init
    offsite.json                  S3-compatible settings, root-only 0600
    secrets/                      host-level secrets, 0700
        cloudflare_tunnel_token   0600, staging only
        r2_access_key_id          0600
        r2_secret_access_key      0600
    tunnel/                       cloudflared stack, staging only
    edge/
        Caddyfile compose.yaml .env
        tenants/<tenant>.caddy    one route file per tenant
        data/ config/ logs/
    tenants/<tenant>/
        compose.yaml .env tenant.json    tenant.json holds no secrets
        config/odoo.conf          0640, owned by uid 10001
        filestore/                Odoo data_dir (filestore + sessions)
        postgres/                 PGDATA
        secrets/                  0700; each secret file 0600
        logs/                     per-tenant Odoo logs
        backups/                  per-tenant backup namespace
```

## Pinned components

| Component | Pin |
| --- | --- |
| Odoo | commit `a2d73c5900d8886d115afe1ccb7f5c97c7e71a97` |
| Python | `3.12.10` (`python:3.12.10-slim-bookworm`, digest pinned) |
| PostgreSQL | `postgres:16.15@sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94` |
| PostgreSQL amd64 manifest | `sha256:485935f94cc7165afa896978809c37b592dc07f0a37d2c8f645f12412d0212c8` |
| Caddy | `caddy:2.10@sha256:c3d7ee5d2b11f9dc54f947f68a734c84e9c9666c92c88a7f30b9cba5da182adb` |
| App image | `ghcr.io/vluxaimkt/vlux-pos:<version>` - production must reference `@sha256:<digest>` |

Database policy is fixed: `CLOUD_DB_ENGINE=PostgreSQL`, `CLOUD_DB_MAJOR=16`,
`CLOUD_DB_TARGET=16.15`, `CLOUD_DB_PRIMARY=STANDARD_POSTGRESQL`,
`SUPABASE_PRIMARY_DB=NO`. `db_host` is a per-tenant setting (`--db-host`), so a
future managed PostgreSQL can replace the container without re-architecting.

## Image

`packaging/cloud/Dockerfile` is multi-stage. The builder carries
`build-essential`, the `-dev` headers and `git`; the runtime stage receives only
the virtualenv, the Odoo tree (with `.git` removed) and the VLUX addons. The
runtime image has no compiler and no git, so it cannot be mutated in place with
`git pull`. It runs as `vlux-pos` (uid/gid 10001) and carries OCI labels for
version, source revision, Odoo commit, build date and edition.

Only the approved products are installed: `vlux_core`, `vlux_mobile_scanner`,
`vlux_owner`. `vlux_facturacion` is **not** enabled as real CFDI. Module
operations are always targeted - never `-u all`.

## Operating

All commands run as root on the host.

```bash
sudo vlux-cloud host-init --acme-email ops@vlux.example
```

Preflights Ubuntu/Docker/Compose, creates `/srv/vlux-pos`, creates the
`vlux-edge` network, starts the edge, checks 80/443 and the filesystem
permission contract. Idempotent.

```bash
sudo vlux-cloud provision braille \
  --domain pos.braille.example \
  --owner-email owner@braille.example \
  --edition cloud_managed \
  --tls-mode public \
  --image ghcr.io/vluxaimkt/vlux-pos@sha256:<digest> \
  --workers 2 --max-cron-threads 1
```

Validates the slug and domain, runs the DNS preflight, creates the directories,
generates secrets, renders the config and compose, starts PostgreSQL, waits for
it, initialises the database, installs the targeted modules, creates the Owner,
starts Odoo, waits for health, writes the edge route, reloads Caddy atomically
and waits for HTTPS. No file is meant to be edited by hand afterwards.

Re-running `provision` for an existing tenant **reconciles**: it never destroys
data and never rotates existing secrets (`provision_mode: RECONCILED`).

| Command | Purpose |
| --- | --- |
| `vlux-cloud list` | tenants on this host |
| `vlux-cloud status [tenant]` | domain, version, image digest, Odoo/PostgreSQL/HTTPS state, last backup, disk usage. Never prints secrets. |
| `vlux-cloud health [tenant]` | PostgreSQL, Odoo `/vlux/health`, edge route, HTTPS. Exit code != 0 when a critical check fails. |
| `vlux-cloud doctor <tenant>` | Read-only diagnosis as one JSON object with sections ODOO (`/vlux/health` + `/vlux/ready`), POSTGRES, DB_CONNECTIONS (`pg_stat_activity` vs `max_connections`), FILESTORE, DISK, EDGE (Caddy route or tunnel), BACKUP_AGE, OFFSITE, WORKERS (vs host CPUs), ADDONS, VERSION. Each section is OK/WARN/FAIL; exit code 0 OK, 1 WARN, 2 FAIL. Never prints secrets. |
| `vlux-cloud backup <tenant> [--offsite]` | consistent dump + filestore + manifest + checksums |
| `vlux-cloud restore <tenant> <archive> --confirm RESTORE_TENANT` | verified restore |
| `vlux-cloud upgrade <tenant> --image <ref@sha256:...>` | backup, swap digest, migrate targeted modules, health, smoke |
| `vlux-cloud disable <tenant> [--stop-database]` | drop edge traffic and stop containers. Never deletes data. |

### TLS

`--tls-mode public` (default) uses normal ACME against the real domain and runs
a DNS preflight first: if the domain does not resolve, provisioning stops rather
than claiming an HTTPS pass. `--tls-mode internal` exists for CI and lab work
only and must never be used for a customer domain.

### Workers and websockets

Odoo runs multiprocess. `workers` and `max_cron_threads` are per tenant
(defaults 2 and 1 - suitable for a small tenant, not a hard-coded 8).
`proxy_mode = True` is always set because the app only ever sees traffic from
the edge. The bus runs on the gevent port 8072, and the edge routes
`/websocket*` to `<tenant>-app:8072` while everything else goes to
`<tenant>-app:8069`.

### Owner credentials

There is no `admin/admin`. Provisioning renames the default administrator to
`--owner-email`, gives it a CSPRNG password and adds it to
`vlux_core.group_vlux_owner`. The password is written only to
`/srv/vlux-pos/tenants/<tenant>/secrets/initial_owner_password` (0600, root
only). It is never printed to stdout, logs or CI output.

**Delivery and deletion procedure**

1. Read it on the host: `sudo cat /srv/vlux-pos/tenants/<t>/secrets/initial_owner_password`.
2. Deliver it to the customer Owner over an out-of-band confidential channel
   (never email, ticket or chat transcript).
3. Have the Owner sign in and change the password immediately.
4. Delete the file: `sudo shred -u /srv/vlux-pos/tenants/<t>/secrets/initial_owner_password`.
5. Record only the fact of delivery - never the value.

Removing the file does not affect the tenant; `status` simply stops listing it
under `secrets_present`.

### Backups

A backup stops the app container for the duration of the dump and the filestore
snapshot, so the database and the filestore agree. A finished `pg_dump` is not
by itself a pass - the archive is checksummed and the checksums are verified on
restore. The archive contains `database.dump`, `filestore.tar.gz`,
`tenant.json`, `manifest.json` and `SHA256SUMS`. It never contains the database
password, the Odoo master password, the Owner password or TLS private keys.

Off-site storage is any S3-compatible endpoint (AWS S3, Cloudflare R2,
Backblaze B2, MinIO). Configure it in `/srv/vlux-pos/offsite.json` (root-only,
0600) or via `VLUX_OFFSITE_ENDPOINT`, `VLUX_OFFSITE_REGION`,
`VLUX_OFFSITE_BUCKET`, `VLUX_OFFSITE_PREFIX`, `VLUX_OFFSITE_ACCESS_KEY_ID`,
`VLUX_OFFSITE_SECRET_ACCESS_KEY`, `VLUX_OFFSITE_SESSION_TOKEN`. Credentials
never live in this repository or in the image. Uploads are SigV4-signed and
verified with a `HEAD` before the backup is recorded as off-site.

```json
{
  "endpoint": "https://s3.us-east-005.backblazeb2.com",
  "region": "us-east-005",
  "bucket": "vlux-pos-backups",
  "prefix": "cloud1",
  "access_key_id": "REEMPLAZAR_ACCESS_KEY",
  "secret_access_key": "REEMPLAZAR_SECRET_KEY"
}
```

### Restore

`restore` verifies every checksum, refuses a backup belonging to another
tenant, refuses to run without `--confirm RESTORE_TENANT`, and takes a
pre-restore safety backup before touching anything. There is no silent
overwrite. It then stops the app, recreates the database, restores the dump,
replaces the filestore, restarts the app and fails loudly if health is
degraded.

### Upgrade and rollback

`upgrade` refuses an image reference without a digest unless `--allow-unpinned`
is passed on a lab host. It always takes a pre-upgrade backup, records the
previous image reference and digest in `previous-release.json` and in
`tenant.json` under `upgrade_history`, then migrates **only** the targeted
modules.

There is no fake database rollback. If the upgrade migrated the schema and then
failed, the command says so explicitly, prints the exact `restore` invocation
using the pre-upgrade backup, preserves the previous digest and exits non-zero.
It never destroys data to recover.

### Disable

`disable` removes the edge route, reloads Caddy and stops the containers. It
does not delete PostgreSQL data, the filestore, backups or secrets, and there is
no destructive delete command in this tool.

## Security contract

* App and PostgreSQL publish no host ports; only the edge binds 80/443.
* PostgreSQL is unreachable from the edge, from the host and from other tenants.
* Every container sets `no-new-privileges` and drops all capabilities
  (PostgreSQL keeps only what its entrypoint needs to `chown` and drop
  privileges). No privileged containers, no host networking, no Docker socket.
* The app runs as uid 10001 on a read-only root filesystem with a tmpfs `/tmp`.
  `--no-readonly-rootfs` exists as an escape hatch for debugging.
* Secrets are CSPRNG-generated per tenant, stored 0600 under a 0700 directory,
  never in git, the image, `tenant.json`, stdout or CI logs.
* Docker logging is capped (`max-size` 10m, `max-file` 5) and Odoo logs are
  per tenant.

`scripts/release/cloud_checks.py` enforces the static half of this contract
offline; `scripts/release/cloud_e2e.sh` proves the runtime half on two live
tenants with positive and negative controls.

## Related technical documents

* [CLOUDFLARE_STAGING.md](CLOUDFLARE_STAGING.md) - run a tenant on your own
  machine behind a Cloudflare Tunnel, with no public IP and no open ports.
* [R2_BACKUPS.md](R2_BACKUPS.md) - S3-compatible off-site backups (Cloudflare
  R2, AWS S3, Backblaze B2, MinIO), retention and the systemd daily timer.
* [MIGRATION_TO_VPS.md](MIGRATION_TO_VPS.md) - move a live tenant to the final
  VPS without losing a sale and without the client recapturing anything.

## Manual gates before production

CI cannot demonstrate real public ACME, a physical phone or a real off-site
provider, so these stay open until a staging VPS run:

* `PUBLIC_DOMAIN_HTTPS=MANUAL_PENDING`
* `CLOUD_HOST_REBOOT=MANUAL_PENDING`
* `REAL_OFFSITE_BACKUP=MANUAL_PENDING`
* `PHONE_CAMERA=MANUAL_PENDING`
* `PHONE_REAL_SCAN=MANUAL_PENDING`
* `PRODUCTION_GO=NOT_YET`

Next step: provision a pristine Ubuntu 24.04 staging VPS with a real test
domain, then validate DNS, Let's Encrypt/ACME, HTTPS, POS, the Owner login, the
phone scanner, off-site backup, restore and a host reboot.
