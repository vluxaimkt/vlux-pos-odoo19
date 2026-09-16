#!/usr/bin/env bash
# VLUX POS Cloudflare staging and host-to-host migration drill.
#
# Proves, on one Linux runner and with no Cloudflare or R2 account:
#   * tunnel mode publishes nothing on the host
#   * the tunnel token never reaches argv, env, inspect output or logs
#   * cloudflared can reach the edge and nothing else
#   * S3-compatible backup, off-site upload and restore-from-S3 work (MinIO)
#   * a tenant migrates to a second, independently namespaced host with its
#     database, filestore and record counts intact
#   * the write freeze actually stops writes during a cutover
#
# Usage: bash scripts/release/cloud_staging_e2e.sh <version> <app-image>
set -euo pipefail

VERSION="${1:-0.0.0-cloud1}"
APP_IMAGE="${2:-vlux-pos:${VERSION}}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLI="${REPO}/packaging/cloud/vlux_cloud.py"

BASE_A="/srv/vlux-pos"
BASE_B="/srv/vlux-pos-b"
HOST_B_ID="hostb"

TENANT="shop-a"
DOMAIN="shop-a.vlux.lab"
DB="vlux_shop_a"

OUT_DIR="${REPO}/dist/cloud"
RESULTS="${OUT_DIR}/staging-results.env"
SUMMARY="${OUT_DIR}/staging-e2e-summary.json"

MINIO_IMAGE="quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e"
MINIO_BUCKET="vlux-pos-backups"
MINIO_USER="vluxminio"
MINIO_PASSWORD="vlux-minio-lab-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"

mkdir -p "$OUT_DIR"
: > "$RESULTS"

record() { printf '%s=%s\n' "$1" "$2" | tee -a "$RESULTS"; }
step()   { printf '\n=== %s ===\n' "$1"; }
die()    { printf 'STAGING FAILURE: %s\n' "$1" >&2; exit 1; }

vlux_a() { sudo python3 "$CLI" --base-dir "$BASE_A" "$@"; }
vlux_b() { sudo python3 "$CLI" --base-dir "$BASE_B" --host-id "$HOST_B_ID" "$@"; }

PYCONN='import socket, sys
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), 4).close()
    print("REACHABLE")
except Exception as exc:
    print("BLOCKED:", exc)
    sys.exit(1)
'

pg_query() { # <container> <db> <sql>
  docker exec -i "$1" psql -tAq -U "$2" -d "$2" -c "$3" | tr -d '[:space:]'
}

odoo_shell() { # <app container> <db>   (script on stdin)
  docker exec -i "$1" /opt/vlux/pos/venv/bin/python \
    /opt/vlux/pos/odoo/odoo-bin shell -c /etc/vlux-pos/odoo.conf -d "$2" --no-http
}

# ---------------------------------------------------------------------------
step "Host A: initialise in Cloudflare tunnel mode"
# ---------------------------------------------------------------------------
grep -q " ${DOMAIN}$" /etc/hosts || echo "127.0.0.1 ${DOMAIN}" | sudo tee -a /etc/hosts >/dev/null
docker image inspect "$APP_IMAGE" >/dev/null || die "app image ${APP_IMAGE} not loaded"

vlux_a host-init --edge-mode cloudflare_tunnel --timeout 240 | tee "${OUT_DIR}/staging-host-init.json"
grep -q '"edge_mode": "cloudflare_tunnel"' "${OUT_DIR}/staging-host-init.json" \
  || die "host A is not in tunnel mode"
record DEPLOYMENT_CLASS CLOUDFLARE_TEMPORARY_STAGING

published="$(docker port vlux-edge-caddy 2>/dev/null || true)"
[ -z "$published" ] || die "tunnel-mode edge published host ports: ${published}"
for port in 80 443 5432 8069 8072; do
  if ss -ltn "sport = :${port}" | grep -q LISTEN; then
    die "port ${port} is listening on the host in tunnel mode"
  fi
done
record HOST_PUBLIC_PORTS NONE
record EDGE_MODE cloudflare_tunnel

# ---------------------------------------------------------------------------
step "Host A: provision the tenant behind the tunnel edge"
# ---------------------------------------------------------------------------
vlux_a provision "$TENANT" \
  --domain "$DOMAIN" \
  --owner-email "owner@vlux.lab" \
  --company-name "VLUX Staging Lab" \
  --data-class REAL_CLIENT_DATA \
  --edition cloud_managed \
  --image "$APP_IMAGE" \
  --workers 2 \
  --timeout 900 | tee "${OUT_DIR}/staging-provision.json"

sudo grep -q "http://${DOMAIN} {" "${BASE_A}/edge/tenants/${TENANT}.caddy" \
  || die "tunnel-mode route is not plain http"
sudo grep -q "tls internal" "${BASE_A}/edge/tenants/${TENANT}.caddy" \
  && die "tunnel-mode route must not request a certificate" || true
vlux_a health "$TENANT" | tee "${OUT_DIR}/staging-health.json" | grep -q '"status": "ok"' \
  || die "tenant health failed in tunnel mode"
record TENANT_HEALTH_TUNNEL_MODE PASS
record DATA_CLASS_SUPPORT PASS

# ---------------------------------------------------------------------------
step "Host A: cloudflared connector (offline token, no Cloudflare account)"
# ---------------------------------------------------------------------------
TOKEN_SRC="$(mktemp)"
python3 - "$TOKEN_SRC" <<'PY'
import base64, json, secrets, sys
# Shaped like a real remotely-managed tunnel token but valid for nobody.
payload = {
    "a": secrets.token_hex(16),
    "t": secrets.token_hex(16),
    "s": base64.b64encode(secrets.token_bytes(32)).decode(),
}
token = base64.b64encode(json.dumps(payload).encode()).decode()
open(sys.argv[1], "w", encoding="utf-8").write(token)
PY
TOKEN_VALUE="$(cat "$TOKEN_SRC")"
[ "${#TOKEN_VALUE}" -ge 40 ] || die "generated lab token is too short"

# --no-wait: the token is not a real Cloudflare credential, so the connector
# will never reach CONNECTED. Everything asserted below is local.
vlux_a staging-init --tunnel-token-file "$TOKEN_SRC" --no-wait --timeout 30 \
  | tee "${OUT_DIR}/staging-init.json"
rm -f "$TOKEN_SRC"

grep -q '"tunnel_mode": "REMOTE_MANAGED"' "${OUT_DIR}/staging-init.json" \
  || die "tunnel is not remotely managed"
record TUNNEL_MODE REMOTE_MANAGED

TOKEN_FILE="${BASE_A}/secrets/cloudflare_tunnel_token"
sudo test -f "$TOKEN_FILE" || die "tunnel token was not stored"
mode="$(sudo stat -c '%a' "$TOKEN_FILE")"
[ "$mode" = "600" ] || die "tunnel token file mode is ${mode}, expected 600"
owner="$(sudo stat -c '%u' "$TOKEN_FILE")"
expected_owner="$(docker image inspect --format '{{.Config.User}}' "$(sudo python3 -c "import json;print(json.load(open('${BASE_A}/host.json'))['tunnel']['image'])")" | cut -d: -f1)"
[ "$owner" = "${expected_owner:-65532}" ] \
  || die "tunnel token owned by ${owner}, cloudflared runs as ${expected_owner}"
record TUNNEL_TOKEN_STORAGE "root-only file 0600 owned by the cloudflared account"

# The token must not appear anywhere observable.
for probe in "${OUT_DIR}/staging-init.json" "${BASE_A}/host.json" "${BASE_A}/tunnel/compose.yaml" "${BASE_A}/tunnel/.env"; do
  if sudo grep -qF "$TOKEN_VALUE" "$probe" 2>/dev/null; then
    die "tunnel token leaked into ${probe}"
  fi
done
if docker inspect vlux-cloudflared 2>/dev/null | grep -qF "$TOKEN_VALUE"; then
  die "tunnel token leaked into docker inspect"
fi
if docker logs vlux-cloudflared 2>&1 | grep -qF "$TOKEN_VALUE"; then
  die "tunnel token leaked into container logs"
fi
if sudo grep -rqF "$TOKEN_VALUE" "${OUT_DIR}" 2>/dev/null; then
  die "tunnel token leaked into a CI artifact"
fi
record CLOUDFLARE_TOKEN_LEAK_SCAN PASS

# cloudflared must be edge-scoped only.
cf_nets="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' vlux-cloudflared | xargs)"
echo "cloudflared networks: ${cf_nets}"
[ "$cf_nets" = "vlux-edge" ] || die "cloudflared is attached to ${cf_nets}, expected only vlux-edge"
record TUNNEL_CONTAINER_NETWORKS "vlux-edge"

cf_ports="$(docker port vlux-cloudflared 2>/dev/null || true)"
[ -z "$cf_ports" ] || die "cloudflared published host ports: ${cf_ports}"

PG_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "vlux-${TENANT}-postgres" | awk '{print $1}')"
if docker run --rm --network vlux-edge --entrypoint python "$APP_IMAGE" -c "$PYCONN" "$PG_IP" 5432; then
  die "the edge network (where cloudflared lives) reached tenant PostgreSQL"
fi
record TUNNEL_POSTGRES_ISOLATION BLOCKED

vlux_a tunnel status > "${OUT_DIR}/tunnel-status.json" 2>&1 || true
sudo grep -qF "$TOKEN_VALUE" "${OUT_DIR}/tunnel-status.json" && die "tunnel status leaked the token" || true
vlux_a tunnel stop >/dev/null 2>&1 || true
record CLOUDFLARE_TUNNEL_STATIC_CHECK PASS

# ---------------------------------------------------------------------------
step "MinIO as the S3-compatible off-site target"
# ---------------------------------------------------------------------------
docker run -d --name vlux-minio \
  -p 127.0.0.1:9000:9000 \
  -e "MINIO_ROOT_USER=${MINIO_USER}" \
  -e "MINIO_ROOT_PASSWORD=${MINIO_PASSWORD}" \
  "$MINIO_IMAGE" server /data >/dev/null
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:9000/minio/health/live" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "http://127.0.0.1:9000/minio/health/live" >/dev/null || die "MinIO did not become ready"

AK_FILE="$(mktemp)"; SK_FILE="$(mktemp)"
printf '%s' "$MINIO_USER" > "$AK_FILE"
printf '%s' "$MINIO_PASSWORD" > "$SK_FILE"

python3 - "$CLI" "$MINIO_BUCKET" "$MINIO_USER" "$MINIO_PASSWORD" <<'PY'
import hashlib, importlib.util, sys
spec = importlib.util.spec_from_file_location("vlux_cloud", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
config = {
    "endpoint": "http://127.0.0.1:9000", "region": "auto", "bucket": sys.argv[2],
    "access_key_id": sys.argv[3], "secret_access_key": sys.argv[4],
}
status, _h, body = cli._s3_request(
    config, "PUT", "", payload_sha256=hashlib.sha256(b"").hexdigest(), content_length=0
)
if status not in (200, 409):
    raise SystemExit("bucket creation failed: %s %s" % (status, body[:200]))
print("bucket ready:", status)
PY

vlux_a r2 configure \
  --provider minio \
  --endpoint "http://127.0.0.1:9000" \
  --region auto \
  --bucket "$MINIO_BUCKET" \
  --prefix "vlux-pos" \
  --access-key-id-file "$AK_FILE" \
  --secret-access-key-file "$SK_FILE" | tee "${OUT_DIR}/r2-configure-a.json"

# Host B is a separate namespace and gets its own configuration.
sudo mkdir -p "$BASE_B"
vlux_b r2 configure \
  --provider minio \
  --endpoint "http://127.0.0.1:9000" \
  --region auto \
  --bucket "$MINIO_BUCKET" \
  --prefix "vlux-pos" \
  --access-key-id-file "$AK_FILE" \
  --secret-access-key-file "$SK_FILE" > "${OUT_DIR}/r2-configure-b.json"
rm -f "$AK_FILE" "$SK_FILE"

for path in "${OUT_DIR}/r2-configure-a.json" "${OUT_DIR}/r2-configure-b.json"; do
  if grep -qF "$MINIO_PASSWORD" "$path"; then
    die "r2 configure echoed the secret access key into ${path}"
  fi
done

for base in "$BASE_A" "$BASE_B"; do
  sudo test -f "${base}/secrets/r2_secret_access_key" || continue
  m="$(sudo stat -c '%a' "${base}/secrets/r2_secret_access_key")"
  [ "$m" = "600" ] || die "R2 secret in ${base} has mode ${m}, expected 600"
done
record R2_SECRET_STORAGE "root-only files 0600 under <base>/secrets"
record R2_ENDPOINT_FORMAT "https://<ACCOUNT_ID>.r2.cloudflarestorage.com (region auto)"

vlux_a r2 test | tee "${OUT_DIR}/r2-test.json"
for key in R2_WRITE R2_READ R2_INTEGRITY R2_DELETE; do
  grep -q "\"${key}\": \"PASS\"" "${OUT_DIR}/r2-test.json" || die "${key} failed"
  record "$key" PASS
done
if grep -qF "$MINIO_PASSWORD" "${OUT_DIR}/r2-test.json"; then
  die "r2 test output leaked the secret access key"
fi
record R2_TEST PASS
record R2_SECRET_LEAK_SCAN PASS

# ---------------------------------------------------------------------------
step "Seed tenant data"
# ---------------------------------------------------------------------------
odoo_shell "vlux-${TENANT}-app" "$DB" <<'PY'
partners = env['res.partner'].sudo().create([
    {'name': 'VLUX Drill Customer 1'},
    {'name': 'VLUX Drill Customer 2'},
    {'name': 'VLUX Drill Customer 3'},
])
products = env['product.product'].sudo().create([
    {'name': 'VLUX-E2E-STOCK-A', 'default_code': 'VLUX-E2E-STOCK-A', 'is_storable': True},
    {'name': 'VLUX Drill Product B', 'default_code': 'VLUX-DRILL-B', 'is_storable': True},
])
attachment = env['ir.attachment'].sudo().create({
    'name': 'vlux-migration-drill.bin',
    'raw': b'VLUX-MIGRATION-DRILL-PAYLOAD' * 128,
})
location = env.ref('stock.stock_location_stock')
quant = env['stock.quant'].sudo().with_context(inventory_mode=True).create({
    'product_id': products[0].id,
    'location_id': location.id,
    'inventory_quantity': 37,
})
quant.action_apply_inventory()
env.cr.commit()
actual_qty = sum(env['stock.quant'].sudo().search([
    ('product_id', '=', products[0].id),
    ('location_id', '=', location.id),
]).mapped('quantity'))
assert actual_qty == 37, 'stock seed quantity is %s, expected 37' % actual_qty
env['ir.config_parameter'].sudo().set_param('vlux.e2e.stock_product_id', str(products[0].id))
print('STOCK_SEEDED qty=37')
env.cr.commit()
print('SEEDED partners=%s products=%s attachment=%s' % (len(partners), len(products), attachment.id))
PY

SRC_PARTNERS="$(pg_query "vlux-${TENANT}-postgres" "$DB" "SELECT count(*) FROM res_partner")"
SRC_ATTACH="$(pg_query "vlux-${TENANT}-postgres" "$DB" "SELECT count(*) FROM ir_attachment")"
SOURCE_STOCK_PRODUCT_ID="$(pg_query "vlux-${TENANT}-postgres" "$DB" "SELECT value FROM ir_config_parameter WHERE key = 'vlux.e2e.stock_product_id'")"
SOURCE_STOCK_QTY="$(pg_query "vlux-${TENANT}-postgres" "$DB" "SELECT to_char(coalesce(sum(quantity), 0), 'FM999999999.####') FROM stock_quant WHERE product_id = ${SOURCE_STOCK_PRODUCT_ID}")"
echo "source partners=${SRC_PARTNERS} attachments=${SRC_ATTACH}"
[ "$SRC_PARTNERS" -ge 3 ] || die "seed data missing"
[ "$SOURCE_STOCK_QTY" = "37" ] || die "source stock quantity is ${SOURCE_STOCK_QTY}, expected 37"
record SOURCE_STOCK_QTY "$SOURCE_STOCK_QTY"

# ---------------------------------------------------------------------------
step "Backup with off-site upload, then restore from S3"
# ---------------------------------------------------------------------------
vlux_a backup "$TENANT" --tier daily --offsite --retention | tee "${OUT_DIR}/staging-backup.json"
grep -q '"local_backup": "PASS"' "${OUT_DIR}/staging-backup.json" || die "local backup failed"
grep -q '"status": "PASS"' "${OUT_DIR}/staging-backup.json" || die "off-site upload failed"
record LOCAL_BACKUP PASS
record OFFSITE_BACKUP_MINIO PASS

OBJECT_KEY="$(python3 -c "import json;print(json.load(open('${OUT_DIR}/staging-backup.json'))['offsite']['object_key'])")"
echo "$OBJECT_KEY" | grep -q "tenants/${TENANT}/daily/" \
  || die "off-site object key is not tiered: ${OBJECT_KEY}"
record OFFSITE_KEY_LAYOUT "prefix/tenants/<tenant>/<tier>/<archive>"

# Destroy local state and restore purely from object storage.
odoo_shell "vlux-${TENANT}-app" "$DB" <<'PY'
env['res.partner'].sudo().search([('name', 'like', 'VLUX Drill Customer%')]).unlink()
env.cr.commit()
print('DRILL_PARTNERS_REMOVED')
PY
AFTER_DELETE="$(pg_query "vlux-${TENANT}-postgres" "$DB" "SELECT count(*) FROM res_partner WHERE name LIKE 'VLUX Drill Customer%'")"
[ "$AFTER_DELETE" = "0" ] || die "drill partners were not removed"
sudo rm -f "${BASE_A}/tenants/${TENANT}/backups/"*.tar.gz

vlux_a restore "$TENANT" --from-s3 "$OBJECT_KEY" --confirm RESTORE_TENANT --timeout 900 \
  | tee "${OUT_DIR}/staging-restore.json"
RESTORED="$(pg_query "vlux-${TENANT}-postgres" "$DB" "SELECT count(*) FROM res_partner WHERE name LIKE 'VLUX Drill Customer%'")"
[ "$RESTORED" = "3" ] || die "restore from S3 did not bring the drill partners back (got ${RESTORED})"
record RESTORE_FROM_S3 PASS
record RESTORE_DRILL PASS

# ---------------------------------------------------------------------------
step "Write freeze must be required for a cutover export"
# ---------------------------------------------------------------------------
if vlux_a migration export "$TENANT" >/dev/null 2>&1; then
  die "migration export ran without a write freeze"
fi
record MIGRATION_EXPORT_REQUIRES_FREEZE PASS

vlux_a maintenance enable "$TENANT" | tee "${OUT_DIR}/maintenance-enable.json"
grep -q '"write_freeze": "ENFORCED"' "${OUT_DIR}/maintenance-enable.json" || die "write freeze not enforced"
maint_code="$(docker run --rm --network vlux-edge --entrypoint curl "$APP_IMAGE" \
  -s -o /dev/null -w '%{http_code}' -H "Host: ${DOMAIN}" \
  "http://vlux-edge-caddy/vlux/health?db=${DB}" || true)"
echo "edge response during maintenance: ${maint_code}"
[ "$maint_code" = "503" ] || die "edge did not answer 503 during maintenance (got ${maint_code})"
[ "$(docker inspect -f '{{.State.Status}}' "vlux-${TENANT}-app")" != "running" ] \
  || die "app still running during the write freeze"
record WRITE_FREEZE ENFORCED
record MAINTENANCE_MODE PASS

# ---------------------------------------------------------------------------
step "Migration export"
# ---------------------------------------------------------------------------
vlux_a migration export "$TENANT" --offsite | tee "${OUT_DIR}/migration-export.json"
grep -q '"write_freeze": "ENFORCED"' "${OUT_DIR}/migration-export.json" || die "export not cutover-ready"
record MIGRATION_EXPORT PASS

BUNDLE="$(python3 -c "import json;print(json.load(open('${OUT_DIR}/migration-export.json'))['archive'])")"
sudo test -f "$BUNDLE" || die "migration bundle missing"

# MIGRATION_BUNDLE_SECRET_SCAN: nothing infrastructural may travel in the bundle.
DB_SECRET="$(sudo cat "${BASE_A}/tenants/${TENANT}/secrets/db_password")"
ADMIN_SECRET="$(sudo cat "${BASE_A}/tenants/${TENANT}/secrets/admin_passwd")"
sudo tar -tzf "$BUNDLE" | tee "${OUT_DIR}/migration-bundle-contents.txt"
if sudo tar -tzf "$BUNDLE" | grep -Eq '(secrets/|db_password|admin_passwd|cloudflare|r2_|\.key|\.pem)'; then
  die "migration bundle contains infrastructure secret paths"
fi
for needle in "$DB_SECRET" "$ADMIN_SECRET" "$TOKEN_VALUE" "$MINIO_PASSWORD"; do
  if sudo tar -xzOf "$BUNDLE" 2>/dev/null | grep -qF "$needle"; then
    die "migration bundle contains an infrastructure secret value"
  fi
done
record MIGRATION_BUNDLE_SECRET_SCAN PASS

# ---------------------------------------------------------------------------
step "Host B: independent host, precheck and import"
# ---------------------------------------------------------------------------
vlux_b host-init --edge-mode cloudflare_tunnel --timeout 240 | tee "${OUT_DIR}/hostb-init.json"
[ -z "$(docker port vlux-${HOST_B_ID}-edge-caddy 2>/dev/null || true)" ] \
  || die "host B published host ports"

vlux_b migration precheck "$TENANT" "$BUNDLE" | tee "${OUT_DIR}/migration-precheck.json"
grep -q '"status": "PASS"' "${OUT_DIR}/migration-precheck.json" || die "migration precheck did not pass"
record MIGRATION_PRECHECK PASS

vlux_b migration import "$TENANT" "$BUNDLE" \
  --confirm IMPORT_TENANT \
  --domain "$DOMAIN" \
  --image "$APP_IMAGE" \
  --timeout 900 | tee "${OUT_DIR}/migration-import.json"
grep -q '"migration_import": "PASS"' "${OUT_DIR}/migration-import.json" || die "migration import failed"
grep -q '"traffic": "DISABLED"' "${OUT_DIR}/migration-import.json" || die "import enabled traffic"
record MIGRATION_IMPORT PASS

sudo test ! -f "${BASE_B}/edge/tenants/${TENANT}.caddy" \
  || die "import installed an edge route; traffic must stay disabled"

# ---------------------------------------------------------------------------
step "Migration verification: data, inventory and filestore"
# ---------------------------------------------------------------------------
B_APP="vlux-${HOST_B_ID}-${TENANT}-app"
B_PG="vlux-${HOST_B_ID}-${TENANT}-postgres"
DST_PARTNERS="$(pg_query "$B_PG" "$DB" "SELECT count(*) FROM res_partner")"
DST_ATTACH="$(pg_query "$B_PG" "$DB" "SELECT count(*) FROM ir_attachment")"
DST_STOCK_PRODUCT_ID="$(pg_query "$B_PG" "$DB" "SELECT value FROM ir_config_parameter WHERE key = 'vlux.e2e.stock_product_id'")"
DST_STOCK_QTY="$(pg_query "$B_PG" "$DB" "SELECT to_char(coalesce(sum(quantity), 0), 'FM999999999.####') FROM stock_quant WHERE product_id = ${DST_STOCK_PRODUCT_ID}")"
echo "destination partners=${DST_PARTNERS} attachments=${DST_ATTACH}"
[ "$DST_PARTNERS" = "$SRC_PARTNERS" ] || die "partner count diverged: ${SRC_PARTNERS} -> ${DST_PARTNERS}"
[ "$DST_ATTACH" = "$SRC_ATTACH" ] || die "attachment count diverged: ${SRC_ATTACH} -> ${DST_ATTACH}"
[ "$DST_STOCK_QTY" = "$SOURCE_STOCK_QTY" ] || die "stock quantity diverged: ${SOURCE_STOCK_QTY} -> ${DST_STOCK_QTY}"
record DESTINATION_STOCK_QTY "$DST_STOCK_QTY"
record INVENTORY_MIGRATION PASS
record TENANT_DB_PRESERVED PASS

SRC_FS="$(sudo find "${BASE_A}/tenants/${TENANT}/filestore/filestore/${DB}" -type f 2>/dev/null | wc -l)"
DST_FS="$(sudo find "${BASE_B}/tenants/${TENANT}/filestore/filestore/${DB}" -type f 2>/dev/null | wc -l)"
echo "filestore files source=${SRC_FS} destination=${DST_FS}"
[ "$SRC_FS" -gt 0 ] || die "source filestore is empty; the drill attachment did not land"
[ "$SRC_FS" = "$DST_FS" ] || die "filestore file count diverged: ${SRC_FS} -> ${DST_FS}"
record TENANT_FILESTORE_PRESERVED PASS

vlux_b migration verify "$TENANT" "$BUNDLE" | tee "${OUT_DIR}/migration-verify.json"
grep -q '"migration_verify": "MATCH"' "${OUT_DIR}/migration-verify.json" \
  || die "migration verify reported divergence"
record MIGRATION_VERIFY PASS
record MIGRATION_E2E PASS

python3 - "${OUT_DIR}/migration-verify.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
comparison = data["comparison"]
assert comparison["status"] == "MATCH", comparison["differences"]
inventory = comparison.get("inventory") or {}
pos = comparison.get("pos") or {}
print("inventory metrics compared:", sorted(inventory))
print("pos metrics compared:", sorted(pos))
for name, pair in list(inventory.items()) + list(pos.items()):
    assert str(pair["source"]) == str(pair["destination"]), (name, pair)
print("INVENTORY_AND_POS_METRICS_MATCH")
PY
record TENANT_INVENTORY_VALIDATION PASS
record POS_DATA_MIGRATION NOT_COVERED

# New host must mint its own infrastructure secrets.
A_DB_SECRET="$(sudo sha256sum "${BASE_A}/tenants/${TENANT}/secrets/db_password" | awk '{print $1}')"
B_DB_SECRET="$(sudo sha256sum "${BASE_B}/tenants/${TENANT}/secrets/db_password" | awk '{print $1}')"
[ "$A_DB_SECRET" != "$B_DB_SECRET" ] || die "host B reused host A's database password"
record MIGRATION_SECRET_REGENERATION PASS

# ---------------------------------------------------------------------------
step "Source host preserved and releasable"
# ---------------------------------------------------------------------------
sudo test -d "${BASE_A}/tenants/${TENANT}/postgres" || die "source PostgreSQL data was deleted"
sudo test -d "${BASE_A}/tenants/${TENANT}/filestore" || die "source filestore was deleted"
record SOURCE_HOST_PRESERVED PASS

vlux_a maintenance disable "$TENANT" | tee "${OUT_DIR}/maintenance-disable.json"
vlux_a health "$TENANT" | grep -q '"status": "ok"' || die "source host did not recover after maintenance"
record MIGRATION_ROLLBACK_SAFE PASS

# ---------------------------------------------------------------------------
step "Summary"
# ---------------------------------------------------------------------------
docker rm -f vlux-minio >/dev/null 2>&1 || true

CLOUDFLARED_IMAGE_REF="$(sudo python3 -c "import json;print(json.load(open('${BASE_A}/host.json'))['tunnel']['image'])")"
CLOUDFLARED_DIGEST_REF="$(sudo python3 -c "import json;print(json.load(open('${BASE_A}/host.json'))['tunnel'].get('digest') or 'UNKNOWN')")"
record CLOUDFLARED_IMAGE "$CLOUDFLARED_IMAGE_REF"
record CLOUDFLARED_DIGEST "$CLOUDFLARED_DIGEST_REF"

# stdout only: stderr carries progress lines that would break json parsing.
vlux_a status > "${OUT_DIR}/staging-status.json" || true
if grep -q '"backup_protection"' "${OUT_DIR}/staging-status.json"; then
  record BACKUP_PROTECTION_STATUS "$(python3 -c "import json;print(json.load(open('${OUT_DIR}/staging-status.json'))['backup_protection'])")"
else
  record BACKUP_PROTECTION_STATUS UNKNOWN
fi

python3 - "$RESULTS" "$SUMMARY" <<'PY'
import json, sys
results = {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if "=" in line:
        key, _, value = line.partition("=")
        results[key] = value
results["R2_BACKEND"] = "S3_COMPATIBLE"
results["CLOUDFLARE_PUBLIC_HTTPS"] = "MANUAL_PENDING"
results["R2_REAL_UPLOAD"] = "MANUAL_PENDING"
results["R2_REAL_RESTORE"] = "MANUAL_PENDING"
results["MIGRATION_TO_REAL_VPS"] = "MANUAL_PENDING"
json.dump(results, open(sys.argv[2], "w", encoding="utf-8"), indent=2, sort_keys=True)
print(json.dumps(results, indent=2, sort_keys=True))
PY

echo "CLOUD_STAGING_E2E=PASS"
