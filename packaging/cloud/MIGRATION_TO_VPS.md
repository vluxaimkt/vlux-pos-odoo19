# VLUX POS — moving a tenant from temporary staging to the final VPS

The temporary Cloudflare staging host and the final VPS run the **same** stack:
same OCI image, same Odoo commit, same addons, same tenant layout, same
PostgreSQL major, same backup format. A migration therefore moves *data*, never
converts formats, and the client keeps their URL, their inventory, their sales,
their customers and their configuration.

`DEPLOYMENT_CLASS` changes from `CLOUDFLARE_TEMPORARY_STAGING` to
`CLOUD_MANAGED_VPS`. Nothing the client sees changes.

## The transaction-cutoff problem

The failure this procedure exists to prevent:

```
12:00  backup taken
12:05  cashier rings up a sale
12:10  backup restored on the new host   ← the 12:05 sale is gone
```

So the **final** export may only run once writes are frozen.
`vlux-cloud migration export` refuses to run otherwise and tells you to enable
maintenance first. `--allow-live` exists for rehearsals only and stamps the
bundle `WRITE_FREEZE=NOT_ENFORCED_REHEARSAL`.

`vlux-cloud maintenance enable <tenant>` makes the edge answer `503` for that
host **and** stops the app container, so neither an HTTP request nor an Odoo
cron job can write while the export runs. It deletes nothing.

## Procedure

```
TEMP HOST                                    NEW VPS
---------                                    -------
1. rehearsal export (--allow-live)
2. prepare VPS ............................. host-init, pull the image
3.                                           migration precheck
4. maintenance enable        ← writes frozen
5. FINAL migration export
6. verify SHA-256
7. transfer (R2 or scp) ....................→
8.                                           migration import  (no traffic yet)
9.                                           migration verify
10.                                          health
11. switch Cloudflare origin / DNS
12.                                          maintenance disable
13. old host preserved, read-only
```

### 1–2. Rehearse, and prepare the VPS

```bash
# temp host, safe to run while the client keeps working
sudo vlux-cloud migration export cliente01 --allow-live

# new VPS
sudo vlux-cloud host-init --edge-mode public_acme --acme-email ops@vlux.example
sudo docker pull ghcr.io/vluxaimkt/vlux-pos@sha256:<digest>
```

### 3. Precheck before you freeze anything

```bash
sudo vlux-cloud migration precheck cliente01 /path/to/rehearsal.tar.gz
```

Compares the bundle against the target: cloud version, **Odoo commit**, required
addons, PostgreSQL major, free disk (roughly 4× the archive) and whether the
tenant is already running here. Any critical mismatch returns `BLOCKED` and the
import is refused, so an incompatible move fails before a freeze, not during it.

### 4–6. Freeze and take the real export

```bash
sudo vlux-cloud maintenance enable cliente01
sudo vlux-cloud migration export cliente01 --offsite
sha256sum /srv/vlux-pos/tenants/cliente01/backups/<archive>
```

The bundle carries the database, the filestore, safe tenant metadata, a manifest
with a SHA-256 per file, and `metrics.json` — record counts captured at the
moment of the freeze. It carries **no** host secrets: no database password, no
Odoo master password, no Cloudflare tunnel token, no R2 credentials, no TLS
private keys.

### 7–8. Transfer and import

```bash
# on the VPS, either from object storage
sudo vlux-cloud migration import cliente01 \
  --from-s3 vlux-pos/tenants/cliente01/migration/<archive> \
  --confirm IMPORT_TENANT \
  --domain pos.cliente.com \
  --image ghcr.io/vluxaimkt/vlux-pos@sha256:<digest>

# or from a file you copied across
sudo vlux-cloud migration import cliente01 /path/to/<archive> \
  --confirm IMPORT_TENANT --domain pos.cliente.com --image ...@sha256:<digest>
```

Import verifies every checksum, re-runs the precheck, **mints brand-new
infrastructure secrets on the target**, restores the database and filestore,
starts Odoo and checks health — and deliberately does **not** install an edge
route. The tenant ends in `state=IMPORTED_NO_TRAFFIC`: running and verifiable,
receiving nothing.

### 9–10. Verify before you send anyone there

```bash
sudo vlux-cloud migration verify cliente01 /path/to/<archive>
sudo vlux-cloud health cliente01
```

`migration verify` recomputes the live counts and compares them with the
metrics recorded at freeze time: partners, users, companies, product templates
and variants, POS orders / lines / sessions / payments, stock quants and their
quantity sum, stock moves, move lines, pickings, account moves, attachments,
installed modules, plus the filestore file count and byte total. It returns
`MATCH` or `DIVERGENT` with the exact differences.

**Limitation, stated plainly:** only *stored* tables are compared. Odoo
recomputes derived stock figures such as `qty_available` at read time from
`stock_move_line`, so they are not compared directly — what a migration must
preserve is the stored data the dump carries, and that is what is checked. If
you need a business-level inventory sign-off, have the client spot-check a
handful of SKUs in the UI after cutover.

### 11–12. Cut over

Point the client's hostname at the new host. On Cloudflare that means editing
the tunnel's public hostname, or turning the DNS record back into a normal
proxied `A`/`AAAA` record for the VPS. **Do this yourself in the dashboard.**
This tool performs no destructive DNS change and holds no Cloudflare API
credential; automating it with the API or Terraform is deliberately left to a
later phase.

Then release the freeze on the new host:

```bash
sudo vlux-cloud maintenance disable cliente01
sudo vlux-cloud health cliente01
```

### 13. Leave the old host alone

Nothing deletes the source tenant. After a successful cutover, stop it but keep
its data:

```bash
# on the temp host
sudo vlux-cloud disable cliente01 --stop-database
```

`disable` removes the edge route and stops the containers. It never deletes the
PostgreSQL data, the filestore, the backups or the secrets. Keep the old host
intact for **at least 30 days** after cutover, and keep one migration bundle
off-site for as long as your retention policy says.

## If something goes wrong

**The destination fails before you switch traffic.** Nothing has changed on the
source. Run `vlux-cloud maintenance disable cliente01` on the temp host and the
client keeps working exactly as before. Fix the VPS and try again.

**Traffic was already switched and the new host took real sales.** Do not roll
back automatically and do not restore the pre-cutover bundle over live data —
that would destroy the sales taken since cutover. The two databases have
diverged; this needs `MANUAL_RECONCILIATION_REQUIRED`: keep both hosts running,
export both, and reconcile the delta deliberately before deciding which side
wins.

This is why step 9 exists. Verify before you switch, not after.

## Keeping the same domain

The client should not have to re-enter a URL. Before: Cloudflare Tunnel →
temporary host. After: Cloudflare → VPS. Same `pos.cliente.com` throughout; only
the origin behind Cloudflare changes.
