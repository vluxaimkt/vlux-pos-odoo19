# VLUX POS — off-site backups on Cloudflare R2

`R2_BACKEND=S3_COMPATIBLE`

Off-site storage is plain S3. Cloudflare R2 is the default because it has no
egress fee, but nothing here is R2-specific: the same configuration works with
AWS S3, Backblaze B2 and MinIO, and CI proves the whole path against MinIO with
no Cloudflare account.

## Step 1 — what you do in the Cloudflare dashboard

1. Sign in and open **R2 Object Storage**. Enable R2 if this is the first time.
2. **Create bucket** → name it `vlux-pos-backups`. Pick a location near the host.
3. Note your **Account ID** (right-hand sidebar, or Workers & Pages → Overview).
   The S3 endpoint is `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`.
4. Go to **R2 → API → Manage API tokens → Create API token**.
   * Permission: **Object Read & Write**
   * Scope: **Apply to specific buckets only** → `vlux-pos-backups`
   * Do **not** grant account-level admin.
5. Create it and copy the **Access Key ID** and **Secret Access Key**. Cloudflare
   shows the secret once.

Never paste these into a chat, a ticket or this repository.

## Step 2 — install the credentials on the host

```bash
sudo install -d -m 0700 /srv/vlux-pos/secrets
sudo install -m 0600 /dev/null /tmp/r2_key_id
sudo install -m 0600 /dev/null /tmp/r2_secret
sudo tee /tmp/r2_key_id >/dev/null    # paste the Access Key ID, Ctrl-D
sudo tee /tmp/r2_secret >/dev/null    # paste the Secret Access Key, Ctrl-D

sudo vlux-cloud r2 configure \
  --account-id <ACCOUNT_ID> \
  --bucket vlux-pos-backups \
  --prefix vlux-pos \
  --access-key-id-file /tmp/r2_key_id \
  --secret-access-key-file /tmp/r2_secret

sudo shred -u /tmp/r2_key_id /tmp/r2_secret
```

The values are copied to `/srv/vlux-pos/secrets/r2_access_key_id` and
`/srv/vlux-pos/secrets/r2_secret_access_key`, both `0600` and root-only.
`/srv/vlux-pos/offsite.json` records only the endpoint, region, bucket, prefix
and the paths of those files — never the values.

For another provider, pass `--endpoint` and `--region` explicitly instead of
`--account-id`:

```bash
sudo vlux-cloud r2 configure --provider aws_s3 \
  --endpoint https://s3.eu-west-1.amazonaws.com --region eu-west-1 \
  --bucket vlux-pos-backups \
  --access-key-id-file ... --secret-access-key-file ...
```

Credentials can also come from the environment
(`VLUX_OFFSITE_ENDPOINT`, `VLUX_OFFSITE_BUCKET`, `VLUX_OFFSITE_ACCESS_KEY_ID`,
`VLUX_OFFSITE_SECRET_ACCESS_KEY`, `VLUX_OFFSITE_REGION`, `VLUX_OFFSITE_PREFIX`,
`VLUX_OFFSITE_SESSION_TOKEN`), which is how CI drives MinIO.

## Step 3 — prove it works

```bash
sudo vlux-cloud r2 test
```

Uploads a small synthetic object, reads it back, compares SHA-256, deletes it
and confirms it is gone. It reports `R2_WRITE`, `R2_READ`, `R2_INTEGRITY` and
`R2_DELETE`, and never touches customer data.

## Object layout

```
<prefix>/tenants/<tenant>/daily/<tenant>-<label>-<timestamp>.tar.gz
<prefix>/tenants/<tenant>/weekly/...
<prefix>/tenants/<tenant>/monthly/...
<prefix>/tenants/<tenant>/migration/...
```

Nothing is written to the bucket root.

If the configured prefix already ends in `tenants` (for example
`--prefix vlux-pos/tenants`), it is not appended again: keys are
`vlux-pos/tenants/<tenant>/...`, never `vlux-pos/tenants/tenants/...`. Older
uploads made under the doubled path are not moved or deleted: each backup
records its exact object key (`backup.offsite_last_object` in `tenant.json`
and the backup record), and `restore --from-s3` takes that key as is.

## What a backup contains

| Included | Excluded |
| --- | --- |
| `database.dump` (pg_dump custom format) | database password |
| `filestore.tar.gz` | Odoo master password |
| `tenant.json` (no secrets) | initial Owner password |
| `manifest.json` with SHA-256 of every file | Cloudflare tunnel token |
| `SHA256SUMS` | R2/S3 credentials |
| `metrics.json` on migration exports | TLS private keys, host credentials |

The app container is stopped for the whole dump **and** the filestore snapshot,
so the two agree. A finished `pg_dump` is not treated as success on its own:
the archive is checksummed, and after upload the object is re-read with `HEAD`
and its size compared before any off-site success is recorded.

Archives larger than 128 MiB upload with S3 multipart in uniform 64 MiB parts —
R2 requires every part except the last to be the same size. A failed multipart
upload is aborted so no incomplete upload is left behind.

## Running a backup

```bash
sudo vlux-cloud backup cliente01 --tier daily --offsite --retention
```

If the local backup succeeds but the upload fails, the local archive is **kept**
and the command reports `LOCAL_BACKUP=PASS` with the off-site failure. The local
copy is never deleted to satisfy an upload.

## Automating it

```bash
sudo install -d -m 0755 /opt/vlux/cloud
sudo cp systemd/vlux-pos-backup@.service systemd/vlux-pos-backup@.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vlux-pos-backup@cliente01.timer
systemctl list-timers 'vlux-pos-backup@*'
journalctl -u vlux-pos-backup@cliente01.service
```

The unit runs at 02:30 with up to 45 minutes of jitter, is `Persistent=true` so
a missed run catches up after a reboot, and reads every credential from the
root-only files — no secret is ever passed on a command line or exported into
the environment.

## Retention

Default policy: **7 daily, 4 weekly, 3 monthly**, applied per tenant.

```bash
sudo vlux-cloud retention cliente01 --dry-run
sudo vlux-cloud retention cliente01
```

Retention only runs after a new archive has been written and verified, and the
newest archive is never a deletion candidate, so the policy cannot remove the
last good backup. Retention applies to local archives; set a matching lifecycle
rule on the bucket if you want the same on the R2 side.

## Restoring

```bash
# from a local archive
sudo vlux-cloud restore cliente01 /srv/vlux-pos/tenants/cliente01/backups/<archive> \
  --confirm RESTORE_TENANT

# straight from object storage
sudo vlux-cloud restore cliente01 \
  --from-s3 vlux-pos/tenants/cliente01/daily/<archive> \
  --confirm RESTORE_TENANT
```

Restore verifies every checksum, refuses a backup belonging to another tenant,
refuses to run without the explicit confirmation token, and takes a pre-restore
safety backup before touching anything.

## Status reporting

`vlux-cloud status` reports `backup_protection` per tenant: last backup, last
off-site success, local archive count and the reasons behind any `DEGRADED`
result. For a tenant marked `REAL_CLIENT_DATA` it adds an explicit warning.

`R2_REAL_UPLOAD` and `R2_REAL_RESTORE` stay `MANUAL_PENDING` until you run the
commands above against your own Cloudflare account. CI proves the mechanism
against MinIO; only you can prove the account.
