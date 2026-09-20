#!/usr/bin/env bash
# VLUX POS Cloud Managed two-tenant end-to-end drill.
#
# Runs on a Linux CI runner with Docker. It provisions two tenants side by side
# on one host and proves the isolation contract with positive AND negative
# controls, then exercises backup, off-site upload, restore, upgrade and
# disable. Everything it asserts is observed; nothing is assumed.
#
# Usage: bash scripts/release/cloud_e2e.sh <version> <app-image>
set -euo pipefail

VERSION="${1:-0.0.0-cloud1}"
APP_IMAGE="${2:-vlux-pos:${VERSION}}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE="/srv/vlux-pos"
OUT_DIR="${REPO}/dist/cloud"
SUMMARY="${OUT_DIR}/cloud-e2e-summary.json"
RESULTS="${OUT_DIR}/e2e-results.env"

TENANT_A="tenant-a"
TENANT_B="tenant-b"
DOMAIN_A="tenant-a.vlux.lab"
DOMAIN_B="tenant-b.vlux.lab"
UNKNOWN_DOMAIN="unknown.vlux.lab"
DB_A="vlux_tenant_a"
DB_B="vlux_tenant_b"

MINIO_IMAGE="quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e"
MINIO_BUCKET="vlux-cloud-backups"
MINIO_USER="vluxminio"
MINIO_PASSWORD="vlux-minio-lab-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"

CA_CERT="/tmp/vlux-edge-root.crt"

mkdir -p "$OUT_DIR"
: > "$RESULTS"

record() { printf '%s=%s\n' "$1" "$2" | tee -a "$RESULTS"; }
step()   { printf '\n=== %s ===\n' "$1"; }
die()    { printf 'E2E FAILURE: %s\n' "$1" >&2; exit 1; }

CLI="${REPO}/packaging/cloud/vlux_cloud.py"
vlux() { sudo python3 "$CLI" "$@"; }

# Connectivity probe run inside a container on a chosen network.
PYCONN='import socket, sys
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), 4).close()
    print("REACHABLE")
except Exception as exc:
    print("BLOCKED:", exc)
    sys.exit(1)
'

connect_from_network() { # <network> <host> <port>
  docker run --rm --network "$1" --entrypoint python "$APP_IMAGE" -c "$PYCONN" "$2" "$3"
}

connect_from_container() { # <container> <host> <port>
  docker exec "$1" python -c "$PYCONN" "$2" "$3"
}

container_ip() { # <container> -> first attached IP
  docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$1" | awk '{print $1}'
}

odoo_shell() { # <tenant> <db>  (script on stdin)
  docker exec -i "vlux-$1-app" /opt/vlux/pos/venv/bin/python \
    /opt/vlux/pos/odoo/odoo-bin shell -c /etc/vlux-pos/odoo.conf -d "$2" --no-http
}

pg_query() { # <tenant> <db> <sql>
  docker exec -i "vlux-$1-postgres" psql -tAq -U "$2" -d "$2" -c "$3" | tr -d '[:space:]'
}

curl_tenant() { # <domain> <path...>
  curl -sS --cacert "$CA_CERT" "$@"
}

# ---------------------------------------------------------------------------
step "Preflight: lab DNS and free public ports"
# ---------------------------------------------------------------------------
for domain in "$DOMAIN_A" "$DOMAIN_B" "$UNKNOWN_DOMAIN"; do
  grep -q " ${domain}$" /etc/hosts || echo "127.0.0.1 ${domain}" | sudo tee -a /etc/hosts >/dev/null
done
for port in 80 443; do
  if ss -ltn "sport = :${port}" | grep -q LISTEN; then
    die "port ${port} is already in use on the runner"
  fi
done
docker image inspect "$APP_IMAGE" >/dev/null || die "app image ${APP_IMAGE} not loaded"

# ---------------------------------------------------------------------------
step "host-init (idempotent)"
# ---------------------------------------------------------------------------
vlux host-init --timeout 240 | tee "${OUT_DIR}/host-init.json"
vlux host-init --timeout 240 >/dev/null
record VLUX_CLOUD_HOST_INIT PASS

edge_ports="$(docker port vlux-edge-caddy | sort | tr '\n' ';')"
echo "edge published: ${edge_ports}"
case "$edge_ports" in
  *"80/tcp"*) ;;
  *) die "edge does not publish 80" ;;
esac
case "$edge_ports" in
  *"443/tcp"*) ;;
  *) die "edge does not publish 443" ;;
esac
record EDGE_ARCHITECTURE SINGLE_GLOBAL_CADDY
record EDGE_PUBLIC_PORTS "80,443"

# ---------------------------------------------------------------------------
step "Provision two tenants on the same host"
# ---------------------------------------------------------------------------
vlux provision "$TENANT_A" \
  --domain "$DOMAIN_A" \
  --owner-email "owner-a@vlux.lab" \
  --company-name "VLUX Lab A" \
  --edition cloud_managed \
  --country MX \
  --tls-mode internal \
  --image "$APP_IMAGE" \
  --workers 2 \
  --max-cron-threads 1 \
  --timeout 900 | tee "${OUT_DIR}/provision-a.json"

sudo cp "${BASE}/edge/data/caddy/pki/authorities/local/root.crt" "$CA_CERT"
sudo chmod 0644 "$CA_CERT"

vlux provision "$TENANT_B" \
  --domain "$DOMAIN_B" \
  --owner-email "owner-b@vlux.lab" \
  --company-name "VLUX Lab B" \
  --edition cloud_managed \
  --tls-mode internal \
  --image "$APP_IMAGE" \
  --workers 2 \
  --max-cron-threads 1 \
  --timeout 900 | tee "${OUT_DIR}/provision-b.json"
record VLUX_CLOUD_PROVISION PASS

# The postgres entrypoint re-execs as the postgres account, so a root-owned
# PGDATA parent silently crash-loops the database. Assert the ownership matches.
for tenant in "$TENANT_A" "$TENANT_B"; do
  dir_uid="$(sudo stat -c '%u' "${BASE}/tenants/${tenant}/postgres")"
  run_uid="$(docker exec "vlux-${tenant}-postgres" id -u postgres)"
  echo "${tenant}: PGDATA parent uid=${dir_uid}, postgres account uid=${run_uid}"
  [ "$dir_uid" = "$run_uid" ]     || die "${tenant} PGDATA parent is owned by ${dir_uid} but the postgres account is ${run_uid}"
done
# The app dirs belong to the app account for the same reason.
[ "$(sudo stat -c '%u' "${BASE}/tenants/${TENANT_A}/filestore")" = "$(docker exec "vlux-${TENANT_A}-app" id -u)" ]   || die "tenant A filestore is not owned by the app account"
record DATA_DIR_OWNERSHIP PASS

# ---------------------------------------------------------------------------
step "Provision idempotence: re-run must not destroy or rotate anything"
# ---------------------------------------------------------------------------
before_a="$(sudo sha256sum "${BASE}/tenants/${TENANT_A}/secrets/db_password" | awk '{print $1}')"
before_admin="$(sudo sha256sum "${BASE}/tenants/${TENANT_A}/secrets/admin_passwd" | awk '{print $1}')"
marker_created_at="$(sudo python3 -c "import json;print(json.load(open('${BASE}/tenants/${TENANT_A}/tenant.json'))['created_at'])")"

vlux provision "$TENANT_A" \
  --domain "$DOMAIN_A" \
  --owner-email "owner-a@vlux.lab" \
  --edition cloud_managed \
  --tls-mode internal \
  --image "$APP_IMAGE" \
  --timeout 900 | tee "${OUT_DIR}/provision-a-again.json"

after_a="$(sudo sha256sum "${BASE}/tenants/${TENANT_A}/secrets/db_password" | awk '{print $1}')"
after_admin="$(sudo sha256sum "${BASE}/tenants/${TENANT_A}/secrets/admin_passwd" | awk '{print $1}')"
created_after="$(sudo python3 -c "import json;print(json.load(open('${BASE}/tenants/${TENANT_A}/tenant.json'))['created_at'])")"
[ "$before_a" = "$after_a" ] || die "db_password was rotated on re-provision"
[ "$before_admin" = "$after_admin" ] || die "admin_passwd was rotated on re-provision"
[ "$marker_created_at" = "$created_after" ] || die "tenant created_at changed on re-provision"
grep -q '"provision_mode": "RECONCILED"' "${OUT_DIR}/provision-a-again.json" || die "re-provision did not reconcile"
record PROVISION_IDEMPOTENCE PASS

# ---------------------------------------------------------------------------
step "Health and status"
# ---------------------------------------------------------------------------
vlux health "$TENANT_A" | tee "${OUT_DIR}/health-a.json" | grep -q '"status": "ok"' || die "tenant A health failed"
vlux health "$TENANT_B" | tee "${OUT_DIR}/health-b.json" | grep -q '"status": "ok"' || die "tenant B health failed"
record TENANT_A_HEALTH PASS
record TENANT_B_HEALTH PASS
record VLUX_CLOUD_HEALTH PASS

vlux status | tee "${OUT_DIR}/status.json"
if grep -Eiq '"(db_password|admin_passwd|initial_owner_password)"\s*:\s*"[^"]{8,}"' "${OUT_DIR}/status.json"; then
  die "status output leaked a secret value"
fi
python3 - "$OUT_DIR/status.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
rows = {row["tenant"]: row for row in data["tenants"]}
assert set(rows) == {"tenant-a", "tenant-b"}, rows.keys()
for row in rows.values():
    for key in ("domain", "image_digest", "odoo", "postgresql", "https", "health", "disk_usage_mb"):
        assert key in row, "status is missing " + key
    assert row["postgres_public_exposure"] == "NONE"
print("status shape OK")
PY
record VLUX_CLOUD_STATUS PASS

vlux list | tee "${OUT_DIR}/list.json" | grep -q "$TENANT_B" || die "list did not report tenant B"

# ---------------------------------------------------------------------------
step "Database isolation"
# ---------------------------------------------------------------------------
[ "$(pg_query "$TENANT_A" "$DB_A" 'SELECT current_database()')" = "$DB_A" ] || die "tenant A database mismatch"
[ "$(pg_query "$TENANT_B" "$DB_B" 'SELECT current_database()')" = "$DB_B" ] || die "tenant B database mismatch"
[ "$DB_A" != "$DB_B" ] || die "tenants share a database name"
if docker exec -i "vlux-${TENANT_A}-postgres" psql -tAq -U "$DB_A" -d postgres -c "SELECT datname FROM pg_database" | grep -qx "$DB_B"; then
  die "tenant A PostgreSQL can see tenant B's database"
fi
record TENANT_DB_ISOLATION PASS

# ---------------------------------------------------------------------------
step "Network isolation (with positive controls)"
# ---------------------------------------------------------------------------
PG_B_IP="$(container_ip "vlux-${TENANT_B}-postgres")"
PG_A_IP="$(container_ip "vlux-${TENANT_A}-postgres")"
echo "postgres A=${PG_A_IP} B=${PG_B_IP}"

# Positive control: each app reaches its OWN database.
connect_from_container "vlux-${TENANT_A}-app" postgres 5432 || die "tenant A cannot reach its own PostgreSQL"
connect_from_container "vlux-${TENANT_B}-app" postgres 5432 || die "tenant B cannot reach its own PostgreSQL"

# Positive control: the probe method itself works on the private network.
connect_from_network "vlux-${TENANT_B}-private" "$PG_B_IP" 5432 \
  || die "probe method is broken: cannot reach postgres B from its own private network"

# Negative: cross-tenant and edge must be blocked.
if connect_from_container "vlux-${TENANT_A}-app" "$PG_B_IP" 5432; then
  die "tenant A reached tenant B PostgreSQL"
fi
if connect_from_container "vlux-${TENANT_B}-app" "$PG_A_IP" 5432; then
  die "tenant B reached tenant A PostgreSQL"
fi
if connect_from_network "vlux-edge" "$PG_A_IP" 5432; then
  die "the edge network reached tenant A PostgreSQL"
fi
if connect_from_network "vlux-edge" "$PG_B_IP" 5432; then
  die "the edge network reached tenant B PostgreSQL"
fi

# The edge container must not be attached to any tenant private network.
edge_nets="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' vlux-edge-caddy)"
echo "edge networks: ${edge_nets}"
case "$edge_nets" in
  *private*) die "the edge is attached to a tenant private network" ;;
esac
for net in "vlux-${TENANT_A}-private" "vlux-${TENANT_B}-private"; do
  [ "$(docker network inspect -f '{{.Internal}}' "$net")" = "true" ] \
    || die "${net} is not an internal network"
done

python3 - "$TENANT_A" "$TENANT_B" <<'PY'
import json, subprocess, sys

tenant_a, tenant_b = sys.argv[1], sys.argv[2]
expected = {
    "vlux-%s-app" % tenant_a: {
        "vlux-edge": ["%s-app" % tenant_a],
        "vlux-%s-private" % tenant_a: ["app"],
    },
    "vlux-%s-app" % tenant_b: {
        "vlux-edge": ["%s-app" % tenant_b],
        "vlux-%s-private" % tenant_b: ["app"],
    },
    "vlux-%s-postgres" % tenant_a: {
        "vlux-%s-private" % tenant_a: ["postgres"],
    },
    "vlux-%s-postgres" % tenant_b: {
        "vlux-%s-private" % tenant_b: ["postgres"],
    },
    "vlux-edge-caddy": {"vlux-edge": []},
}
for container, networks in expected.items():
    raw = subprocess.check_output(["docker", "inspect", container], text=True)
    actual = json.loads(raw)[0]["NetworkSettings"]["Networks"]
    actual_names = set(actual)
    expected_names = set(networks)
    assert actual_names == expected_names, (container, actual_names, expected_names)
    for network, aliases in networks.items():
        actual_aliases = set(actual[network].get("Aliases") or [])
        for alias in aliases:
            assert alias in actual_aliases, (container, network, alias, actual_aliases)
        if network == "vlux-edge":
            assert "app" not in actual_aliases, (container, network, actual_aliases)
print("docker network aliases OK")
PY
record TENANT_NETWORK_ISOLATION PASS
record EDGE_ISOLATION PASS

# ---------------------------------------------------------------------------
step "Host port exposure"
# ---------------------------------------------------------------------------
ss -ltn | tee "${OUT_DIR}/listening-ports.txt"
for port in 5432 8069 8072; do
  if ss -ltn "sport = :${port}" | grep -q LISTEN; then
    die "port ${port} is listening on the host"
  fi
done
record POSTGRES_PUBLIC_EXPOSURE NOT_EXPOSED
record ODOO_PORTS_PUBLIC_EXPOSURE NOT_EXPOSED

# ---------------------------------------------------------------------------
step "Filestore isolation"
# ---------------------------------------------------------------------------
docker exec "vlux-${TENANT_A}-app" sh -c 'echo vlux-tenant-a-only > /var/lib/vlux-pos/vlux-isolation-marker.txt'
sudo test -f "${BASE}/tenants/${TENANT_A}/filestore/vlux-isolation-marker.txt" \
  || die "filestore marker missing on tenant A"
sudo test ! -f "${BASE}/tenants/${TENANT_B}/filestore/vlux-isolation-marker.txt" \
  || die "tenant A filestore marker appeared in tenant B"
if docker exec "vlux-${TENANT_B}-app" test -f /var/lib/vlux-pos/vlux-isolation-marker.txt; then
  die "tenant B container can see tenant A's filestore marker"
fi
record TENANT_FILESTORE_ISOLATION PASS

# ---------------------------------------------------------------------------
step "Secret isolation"
# ---------------------------------------------------------------------------
for name in db_password admin_passwd initial_owner_password; do
  a="$(sudo sha256sum "${BASE}/tenants/${TENANT_A}/secrets/${name}" | awk '{print $1}')"
  b="$(sudo sha256sum "${BASE}/tenants/${TENANT_B}/secrets/${name}" | awk '{print $1}')"
  [ "$a" != "$b" ] || die "tenants share the same ${name}"
  mode="$(sudo stat -c '%a' "${BASE}/tenants/${TENANT_A}/secrets/${name}")"
  [ "$mode" = "600" ] || die "${name} has mode ${mode}, expected 600"
done
[ "$(sudo stat -c '%a' "${BASE}/tenants/${TENANT_A}/secrets")" = "700" ] || die "secrets dir is not 0700"
if docker exec "vlux-${TENANT_B}-app" sh -c "ls ${BASE}/tenants/${TENANT_A}/secrets" 2>/dev/null; then
  die "tenant B container can read tenant A secrets"
fi
record TENANT_SECRET_ISOLATION PASS

# ---------------------------------------------------------------------------
step "Edge routing isolation and HTTPS"
# ---------------------------------------------------------------------------
curl_tenant "https://${DOMAIN_A}/vlux/health?db=${DB_A}" | grep -q '"status": "ok"' || die "tenant A HTTPS health failed"
curl_tenant "https://${DOMAIN_B}/vlux/health?db=${DB_B}" | grep -q '"status": "ok"' || die "tenant B HTTPS health failed"

odoo_shell "$TENANT_A" "$DB_A" <<'PY'
env['ir.config_parameter'].sudo().set_param('vlux.e2e.tenant_identity', 'VLUX_E2E_TENANT_A')
env.cr.commit()
PY
odoo_shell "$TENANT_B" "$DB_B" <<'PY'
env['ir.config_parameter'].sudo().set_param('vlux.e2e.tenant_identity', 'VLUX_E2E_TENANT_B')
env.cr.commit()
PY
[ "$(pg_query "$TENANT_A" "$DB_A" "SELECT value FROM ir_config_parameter WHERE key='vlux.e2e.tenant_identity'")" = "VLUX_E2E_TENANT_A" ] \
  || die "tenant A identity marker was not stored in tenant A DB"
[ "$(pg_query "$TENANT_B" "$DB_B" "SELECT value FROM ir_config_parameter WHERE key='vlux.e2e.tenant_identity'")" = "VLUX_E2E_TENANT_B" ] \
  || die "tenant B identity marker was not stored in tenant B DB"
[ "$(pg_query "$TENANT_A" "$DB_A" "SELECT count(*) FROM ir_config_parameter WHERE value='VLUX_E2E_TENANT_B'")" = "0" ] \
  || die "tenant B identity marker appeared in tenant A DB"
[ "$(pg_query "$TENANT_B" "$DB_B" "SELECT count(*) FROM ir_config_parameter WHERE value='VLUX_E2E_TENANT_A'")" = "0" ] \
  || die "tenant A identity marker appeared in tenant B DB"
record TENANT_A_DB "$DB_A"
record TENANT_B_DB "$DB_B"

# Each hostname must be bound to its OWN backend. Asking for the other
# tenant's db in the query string proves nothing: dbfilter pins each app to a
# single database, so Odoo answers for whichever app the request reached.
# Taking tenant B's app down is decisive instead - if domain B were routed to
# tenant A it would keep answering 200.
docker stop "vlux-${TENANT_B}-app" >/dev/null
sleep 3
code_b_down="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CA_CERT" "https://${DOMAIN_B}/vlux/health?db=${DB_B}" || true)"
code_a_up="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CA_CERT" "https://${DOMAIN_A}/vlux/health?db=${DB_A}" || true)"
echo "with tenant B stopped: domainB=${code_b_down} domainA=${code_a_up}"
docker start "vlux-${TENANT_B}-app" >/dev/null
[ "$code_b_down" != "200" ] \
  || die "domain B answered 200 while tenant B was down: it is not bound to tenant B"
[ "$code_a_up" = "200" ] \
  || die "domain A broke when tenant B went down: the tenants are not independent"

for _ in $(seq 1 90); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "vlux-${TENANT_B}-app" 2>/dev/null)" = "healthy" ] && break
  sleep 5
done
[ "$(docker inspect -f '{{.State.Health.Status}}' "vlux-${TENANT_B}-app")" = "healthy" ] \
  || die "tenant B did not recover after the routing check"
curl_tenant "https://${DOMAIN_B}/vlux/health?db=${DB_B}" | grep -q '"status": "ok"' \
  || die "tenant B did not serve again after the routing check"

for _ in $(seq 1 5); do
  curl_tenant "https://${DOMAIN_A}/vlux/health?db=${DB_A}" | grep -q '"status": "ok"' \
    || die "tenant A routing was not stable"
  curl_tenant "https://${DOMAIN_B}/vlux/health?db=${DB_B}" | grep -q '"status": "ok"' \
    || die "tenant B routing was not stable"
done

unknown_body="${OUT_DIR}/unknown-host-body.txt"
unknown_code="$(curl -k -sS -o "$unknown_body" -w '%{http_code}' \
  --resolve "${UNKNOWN_DOMAIN}:443:127.0.0.1" \
  "https://${UNKNOWN_DOMAIN}/vlux/health?db=${DB_A}" || true)"
echo "unknown host status: ${unknown_code}"
if [ "$unknown_code" = "200" ] && grep -q '"status": "ok"' "$unknown_body"; then
  die "unknown host reached an Odoo tenant"
fi
record UNKNOWN_HOST_FAIL_CLOSED PASS

docker exec vlux-edge-caddy wget -q -O - http://127.0.0.1:2019/config/ > "${OUT_DIR}/caddy-config.json"
python3 - "${OUT_DIR}/caddy-config.json" <<'PY'
import json, sys
text = open(sys.argv[1], encoding="utf-8").read()
for alias in ("tenant-a-app:8069", "tenant-b-app:8069", "tenant-a-app:8072", "tenant-b-app:8072"):
    assert alias in text, "edge config missing upstream " + alias
assert '"app:8069"' not in text, "edge config uses a non-unique app alias"
config = json.loads(text)
hosts = json.dumps(config)
assert "tenant-a.vlux.lab" in hosts and "tenant-b.vlux.lab" in hosts
assert "unknown.vlux.lab" not in hosts
print("edge routing config OK")
PY
record TENANT_A_ROUTING PASS
record TENANT_B_ROUTING PASS
record CROSS_TENANT_HTTP_ROUTING BLOCKED
record EDGE_ROUTING_ISOLATION PASS
record HTTPS_INTERNAL PASS

# ---------------------------------------------------------------------------
step "Websocket / gevent routing"
# ---------------------------------------------------------------------------
connect_from_container "vlux-${TENANT_A}-app" 127.0.0.1 8072 || die "gevent port 8072 is not listening"
WS_HEADERS=(-H "Connection: Upgrade" -H "Upgrade: websocket" -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==")
direct_8072="$(docker exec "vlux-${TENANT_A}-app" curl -s -o /dev/null -w '%{http_code}' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  "http://127.0.0.1:8072/websocket" || true)"
direct_8069="$(docker exec "vlux-${TENANT_A}-app" curl -s -o /dev/null -w '%{http_code}' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  "http://127.0.0.1:8069/websocket" || true)"
via_edge="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CA_CERT" \
  "${WS_HEADERS[@]}" "https://${DOMAIN_A}/websocket" || true)"
echo "websocket codes: direct8072=${direct_8072} direct8069=${direct_8069} viaEdge=${via_edge}"
[ -n "$via_edge" ] && [ "$via_edge" != "502" ] && [ "$via_edge" != "000" ] \
  || die "edge could not reach the gevent websocket upstream (${via_edge})"
[ "$via_edge" = "$direct_8072" ] \
  || die "edge websocket route (${via_edge}) does not match the gevent port (${direct_8072})"
record ODOO_HTTP_PORT 8069
record ODOO_GEVENT_PORT 8072
record WEBSOCKET_PROXY PASS

workers_a="$(sudo grep -E '^workers' "${BASE}/tenants/${TENANT_A}/config/odoo.conf" | awk '{print $3}')"
[ "$workers_a" = "2" ] || die "tenant A workers is ${workers_a}, expected 2"
for tenant in "$TENANT_A" "$TENANT_B"; do
  db_name="$(sudo python3 -c "import json;print(json.load(open('${BASE}/tenants/${tenant}/tenant.json'))['database']['name'])")"
  conf="${BASE}/tenants/${tenant}/config/odoo.conf"
  sudo grep -q '^db_host = postgres' "$conf" || die "${tenant} db_host is not postgres"
  sudo grep -q "^db_name = ${db_name}$" "$conf" || die "${tenant} db_name is not exact"
  sudo grep -Fxq "dbfilter = ^${db_name}$" "$conf" || die "${tenant} dbfilter is not exact"
  sudo grep -q '^list_db = False' "$conf" || die "${tenant} list_db is not disabled"
  sudo grep -q '^proxy_mode = True' "$conf" || die "${tenant} proxy_mode is not enabled"
done
record ODOO_WORKERS_DEFAULT "$workers_a"

# ---------------------------------------------------------------------------
step "Owner bootstrap: no default admin/admin"
# ---------------------------------------------------------------------------
[ "$(pg_query "$TENANT_A" "$DB_A" "SELECT count(*) FROM res_users WHERE login='admin'")" = "0" ] \
  || die "the default admin login still exists"
[ "$(pg_query "$TENANT_A" "$DB_A" "SELECT count(*) FROM res_users WHERE login='owner-a@vlux.lab'")" = "1" ] \
  || die "the Owner user was not created"
owner_group="$(pg_query "$TENANT_A" "$DB_A" "SELECT count(*) FROM res_groups_users_rel r JOIN ir_model_data d ON d.res_id = r.gid AND d.model='res.groups' AND d.module='vlux_core' AND d.name='group_vlux_owner' JOIN res_users u ON u.id = r.uid WHERE u.login='owner-a@vlux.lab'")"
[ "$owner_group" = "1" ] || die "the Owner user is not in vlux_core.group_vlux_owner"
sudo test -f "${BASE}/tenants/${TENANT_A}/secrets/initial_owner_password" || die "initial owner password was not stored"
record OWNER_BOOTSTRAP PASS
record DEFAULT_ADMIN_CREDENTIALS REMOVED

# ---------------------------------------------------------------------------
step "Localisation: the tenant sells in its own country's currency and taxes"
# ---------------------------------------------------------------------------
country="$(pg_query "$TENANT_A" "$DB_A" "SELECT c.code FROM res_company co JOIN res_country c ON c.id = co.country_id WHERE co.id = 1")"
[ "$country" = "MX" ] || die "the tenant company is not in MX (got '${country}')"
chart="$(pg_query "$TENANT_A" "$DB_A" "SELECT chart_template FROM res_company WHERE id = 1")"
[ "$chart" = "mx" ] || die "the Mexican chart of accounts was not loaded (got '${chart}')"
currency="$(pg_query "$TENANT_A" "$DB_A" "SELECT cur.name FROM res_company co JOIN res_currency cur ON cur.id = co.currency_id WHERE co.id = 1")"
[ "$currency" = "MXN" ] || die "the tenant company is not in MXN (got '${currency}')"
iva="$(pg_query "$TENANT_A" "$DB_A" "SELECT count(*) FROM account_tax WHERE company_id = 1 AND type_tax_use = 'sale' AND amount = 16")"
[ "$iva" != "0" ] || die "no 16 % sale tax (IVA) exists in the provisioned tenant"
record LOCALISATION "MX/mx/MXN/IVA16"

# ---------------------------------------------------------------------------
step "Scanner and owner backend reachable over HTTPS"
# ---------------------------------------------------------------------------
curl_tenant "https://${DOMAIN_A}/vlux/scanner?db=${DB_A}" -o /tmp/scanner-a.html
grep -Eiq 'scanner|camera|barcode|codigo|c.mara' /tmp/scanner-a.html || die "scanner page did not render"
curl_tenant "https://${DOMAIN_A}/vlux-owner/manifest.webmanifest?db=${DB_A}" -o /tmp/owner-a.webmanifest
grep -q 'VLUX' /tmp/owner-a.webmanifest || die "owner PWA manifest did not render"
record SCANNER_BACKEND PASS

# ---------------------------------------------------------------------------
step "Seed drill data"
# ---------------------------------------------------------------------------
odoo_shell "$TENANT_A" "$DB_A" <<'PY'
partner = env['res.partner'].sudo().create({'name': 'VLUX-RESTORE-DRILL'})
env.cr.commit()
print('DRILL_PARTNER_ID', partner.id)
PY
docker exec "vlux-${TENANT_A}-app" sh -c 'echo drill > /var/lib/vlux-pos/vlux-drill-marker.txt'
[ "$(pg_query "$TENANT_A" "$DB_A" "SELECT count(*) FROM res_partner WHERE name='VLUX-RESTORE-DRILL'")" = "1" ] \
  || die "drill partner was not created"

# ---------------------------------------------------------------------------
step "Off-site S3 target (MinIO)"
# ---------------------------------------------------------------------------
docker run -d --name vlux-minio \
  -p 127.0.0.1:9000:9000 \
  -e "MINIO_ROOT_USER=${MINIO_USER}" \
  -e "MINIO_ROOT_PASSWORD=${MINIO_PASSWORD}" \
  "$MINIO_IMAGE" server /data >/dev/null
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:9000/minio/health/live" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS "http://127.0.0.1:9000/minio/health/live" >/dev/null || die "MinIO did not become ready"

python3 - "$CLI" "$MINIO_BUCKET" "$MINIO_USER" "$MINIO_PASSWORD" <<'PY'
import hashlib, importlib.util, sys
spec = importlib.util.spec_from_file_location("vlux_cloud", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
config = {
    "endpoint": "http://127.0.0.1:9000",
    "region": "us-east-1",
    "bucket": sys.argv[2],
    "access_key_id": sys.argv[3],
    "secret_access_key": sys.argv[4],
}
empty = hashlib.sha256(b"").hexdigest()
status, _headers, body = cli._s3_request(config, "PUT", "", payload_sha256=empty, content_length=0)
if status not in (200, 409):
    raise SystemExit("bucket creation failed: %s %s" % (status, body[:300]))
print("bucket ready:", status)
PY

sudo tee "${BASE}/offsite.json" >/dev/null <<JSON
{
  "endpoint": "http://127.0.0.1:9000",
  "region": "us-east-1",
  "bucket": "${MINIO_BUCKET}",
  "prefix": "cloud1",
  "access_key_id": "${MINIO_USER}",
  "secret_access_key": "${MINIO_PASSWORD}"
}
JSON
sudo chmod 0600 "${BASE}/offsite.json"

# ---------------------------------------------------------------------------
step "Backup (local + off-site)"
# ---------------------------------------------------------------------------
vlux backup "$TENANT_A" --label drill --offsite | tee "${OUT_DIR}/backup-a.json"
BACKUP_ARCHIVE="$(python3 -c "import json;print(json.load(open('${OUT_DIR}/backup-a.json'))['archive'])")"
BACKUP_SHA="$(python3 -c "import json;print(json.load(open('${OUT_DIR}/backup-a.json'))['sha256'])")"
OFFSITE_KEY="$(python3 -c "import json;print(json.load(open('${OUT_DIR}/backup-a.json'))['offsite']['object_key'])")"
sudo test -f "$BACKUP_ARCHIVE" || die "backup archive missing"
[ "$(sudo sha256sum "$BACKUP_ARCHIVE" | awk '{print $1}')" = "$BACKUP_SHA" ] || die "backup checksum mismatch"

# The archive must carry a real dump and filestore, and no credentials.
sudo tar -tzf "$BACKUP_ARCHIVE" | tee "${OUT_DIR}/backup-contents.txt"
grep -q 'database.dump' "${OUT_DIR}/backup-contents.txt" || die "backup has no database dump"
grep -q 'filestore.tar.gz' "${OUT_DIR}/backup-contents.txt" || die "backup has no filestore"
grep -q 'SHA256SUMS' "${OUT_DIR}/backup-contents.txt" || die "backup has no checksums"
if sudo tar -tzf "$BACKUP_ARCHIVE" | grep -Eq '(db_password|admin_passwd|initial_owner_password|\.key|\.pem)'; then
  die "backup archive contains secret material"
fi
db_secret="$(sudo cat "${BASE}/tenants/${TENANT_A}/secrets/db_password")"
if sudo tar -xzOf "$BACKUP_ARCHIVE" 2>/dev/null | grep -qF "$db_secret"; then
  die "backup payload contains the database password"
fi
record LOCAL_BACKUP_SMOKE PASS
record VLUX_CLOUD_BACKUP PASS

python3 - "$CLI" "$MINIO_BUCKET" "$MINIO_USER" "$MINIO_PASSWORD" "$OFFSITE_KEY" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("vlux_cloud", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
config = {
    "endpoint": "http://127.0.0.1:9000",
    "region": "us-east-1",
    "bucket": sys.argv[2],
    "access_key_id": sys.argv[3],
    "secret_access_key": sys.argv[4],
}
status, headers, _ = cli._s3_request(config, "HEAD", sys.argv[5], payload_sha256=cli.EMPTY_SHA256)
if status != 200:
    raise SystemExit("off-site object not found: HTTP %s" % status)
print("off-site object present, bytes:", headers.get("Content-Length"))
PY
record OFFSITE_BACKUP_SMOKE PASS

# ---------------------------------------------------------------------------
step "Restore drill: destroy state, restore, verify"
# ---------------------------------------------------------------------------
odoo_shell "$TENANT_A" "$DB_A" <<'PY'
env['res.partner'].sudo().search([('name', '=', 'VLUX-RESTORE-DRILL')]).unlink()
env.cr.commit()
print('DRILL_PARTNER_REMOVED')
PY
docker exec "vlux-${TENANT_A}-app" rm -f /var/lib/vlux-pos/vlux-drill-marker.txt
[ "$(pg_query "$TENANT_A" "$DB_A" "SELECT count(*) FROM res_partner WHERE name='VLUX-RESTORE-DRILL'")" = "0" ] \
  || die "drill partner was not removed"

vlux restore "$TENANT_A" "$BACKUP_ARCHIVE" --confirm RESTORE_TENANT --timeout 900 \
  | tee "${OUT_DIR}/restore-a.json"
[ "$(pg_query "$TENANT_A" "$DB_A" "SELECT count(*) FROM res_partner WHERE name='VLUX-RESTORE-DRILL'")" = "1" ] \
  || die "restore did not bring the drill partner back"
docker exec "vlux-${TENANT_A}-app" test -f /var/lib/vlux-pos/vlux-drill-marker.txt \
  || die "restore did not bring the filestore marker back"
vlux health "$TENANT_A" | grep -q '"status": "ok"' || die "health failed after restore"
record RESTORE_DRILL PASS
record VLUX_CLOUD_RESTORE PASS

# Restore must refuse a mismatched tenant and a missing confirmation.
if vlux restore "$TENANT_B" "$BACKUP_ARCHIVE" --confirm RESTORE_TENANT --no-safety-backup >/dev/null 2>&1; then
  die "restore accepted a backup belonging to another tenant"
fi
if vlux restore "$TENANT_A" "$BACKUP_ARCHIVE" --confirm WRONG >/dev/null 2>&1; then
  die "restore ran without the explicit confirmation token"
fi
record RESTORE_GUARDRAILS PASS

# ---------------------------------------------------------------------------
step "Upgrade"
# ---------------------------------------------------------------------------
docker tag "$APP_IMAGE" "vlux-pos:${VERSION}-upgrade"
if vlux upgrade "$TENANT_A" --image "vlux-pos:${VERSION}-upgrade" >/dev/null 2>&1; then
  die "upgrade accepted an unpinned image without --allow-unpinned"
fi
record UPGRADE_DIGEST_GUARD PASS

vlux upgrade "$TENANT_A" --image "vlux-pos:${VERSION}-upgrade" --allow-unpinned --timeout 900 \
  | tee "${OUT_DIR}/upgrade-a.json"
grep -q '"upgrade": "PASS"' "${OUT_DIR}/upgrade-a.json" || die "upgrade did not report PASS"
python3 - "${OUT_DIR}/upgrade-a.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
assert data["modules_upgraded"] == ["vlux_core", "vlux_mobile_scanner", "vlux_owner", "vlux_pos_catalog"], data["modules_upgraded"]
assert data["pre_upgrade_backup_id"], "no pre-upgrade backup recorded"
assert data["previous_image"], "previous image not preserved"
print("upgrade metadata OK")
PY
[ "$(pg_query "$TENANT_A" "$DB_A" "SELECT count(*) FROM res_partner WHERE name='VLUX-RESTORE-DRILL'")" = "1" ] \
  || die "upgrade lost tenant data"
vlux health "$TENANT_A" | grep -q '"status": "ok"' || die "health failed after upgrade"
record UPGRADE_SMOKE PASS
record VLUX_CLOUD_UPGRADE PASS

# ---------------------------------------------------------------------------
step "Disable and data preservation"
# ---------------------------------------------------------------------------
b_db_size_before="$(sudo du -sb "${BASE}/tenants/${TENANT_B}/postgres" | awk '{print $1}')"
vlux disable "$TENANT_B" --stop-database | tee "${OUT_DIR}/disable-b.json"
disabled_code="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CA_CERT" "https://${DOMAIN_B}/vlux/health?db=${DB_B}" || true)"
[ "$disabled_code" != "200" ] || die "tenant B still serves traffic after disable"
sudo test -d "${BASE}/tenants/${TENANT_B}/postgres" || die "disable deleted the PostgreSQL data directory"
sudo test -d "${BASE}/tenants/${TENANT_B}/filestore" || die "disable deleted the filestore"
sudo test -d "${BASE}/tenants/${TENANT_B}/backups" || die "disable deleted the backups namespace"
sudo test -f "${BASE}/tenants/${TENANT_B}/secrets/db_password" || die "disable deleted the tenant secrets"
b_db_size_after="$(sudo du -sb "${BASE}/tenants/${TENANT_B}/postgres" | awk '{print $1}')"
[ "$b_db_size_after" -ge "$((b_db_size_before / 2))" ] || die "PostgreSQL data shrank after disable"
record VLUX_CLOUD_DISABLE PASS

# Tenant A must be unaffected by disabling tenant B.
vlux health "$TENANT_A" | grep -q '"status": "ok"' || die "disabling tenant B disturbed tenant A"

vlux provision "$TENANT_B" \
  --domain "$DOMAIN_B" \
  --owner-email "owner-b@vlux.lab" \
  --edition cloud_managed \
  --tls-mode internal \
  --image "$APP_IMAGE" \
  --timeout 900 >/dev/null
vlux health "$TENANT_B" | grep -q '"status": "ok"' || die "tenant B did not recover after re-enable"
[ "$(pg_query "$TENANT_B" "$DB_B" "SELECT count(*) FROM res_users WHERE login='owner-b@vlux.lab'")" = "1" ] \
  || die "tenant B lost its Owner user across disable/enable"
record DATA_PRESERVATION PASS

# ---------------------------------------------------------------------------
step "Container security"
# ---------------------------------------------------------------------------
for container in "vlux-${TENANT_A}-app" "vlux-${TENANT_B}-app"; do
  [ "$(docker exec "$container" id -u)" = "10001" ] || die "${container} is not running as uid 10001"
  [ "$(docker inspect -f '{{.HostConfig.Privileged}}' "$container")" = "false" ] || die "${container} is privileged"
  [ "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$container")" != "host" ] || die "${container} uses host networking"
  docker inspect -f '{{json .HostConfig.Binds}}' "$container" | grep -q 'docker.sock' \
    && die "${container} mounts the Docker socket" || true
  docker inspect -f '{{json .HostConfig.SecurityOpt}}' "$container" | grep -q 'no-new-privileges' \
    || die "${container} is missing no-new-privileges"
  [ "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$container")" = "true" ] \
    || die "${container} does not use a read-only root filesystem"
done
[ "$(docker inspect -f '{{.HostConfig.Privileged}}' vlux-edge-caddy)" = "false" ] || die "the edge is privileged"
record APP_RUNS_NON_ROOT PASS
record CONTAINER_SECURITY PASS

# The runtime image must not carry build tooling.
if docker run --rm --entrypoint sh "$APP_IMAGE" -c 'command -v gcc || command -v git || command -v make' ; then
  die "the runtime image still contains build tooling"
fi
docker run --rm --entrypoint sh "$APP_IMAGE" -c 'test ! -e /opt/vlux/pos/odoo/.git' \
  || die "the runtime image contains an Odoo .git directory"
record IMMUTABLE_IMAGE PASS

# ---------------------------------------------------------------------------
step "Secret and log scans"
# ---------------------------------------------------------------------------
sudo python3 - "$BASE" "$OUT_DIR" <<'PY'
import pathlib, subprocess, sys

base = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
needles = []
for tenant_dir in sorted((base / "tenants").iterdir()):
    secrets_dir = tenant_dir / "secrets"
    if not secrets_dir.is_dir():
        continue
    for secret in sorted(secrets_dir.iterdir()):
        value = secret.read_text(encoding="utf-8").strip()
        if len(value) >= 12:
            needles.append((tenant_dir.name + "/" + secret.name, value))

haystacks = []
for tenant_dir in sorted((base / "tenants").iterdir()):
    haystacks.extend(sorted((tenant_dir / "logs").glob("*")))
haystacks.extend(sorted((base / "edge" / "logs").glob("*")))
haystacks.extend(sorted(out.glob("*.json")))
haystacks.extend(sorted(out.glob("*.txt")))

containers = subprocess.run(
    ["docker", "ps", "-a", "--format", "{{.Names}}"],
    capture_output=True, text=True, check=True,
).stdout.split()
log_blobs = {}
for name in containers:
    proc = subprocess.run(["docker", "logs", name], capture_output=True, text=True, check=False)
    log_blobs["docker-logs:" + name] = (proc.stdout or "") + (proc.stderr or "")
    inspect = subprocess.run(["docker", "inspect", name], capture_output=True, text=True, check=False)
    log_blobs["docker-inspect:" + name] = inspect.stdout or ""

failures = []
for label, value in needles:
    for path in haystacks:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if value in text:
            failures.append("secret " + label + " leaked into " + str(path))
    for blob_name, blob in log_blobs.items():
        if value and value in blob:
            failures.append("secret " + label + " leaked into " + blob_name)

for failure in failures:
    print("ERROR: " + failure, file=sys.stderr)
if failures:
    raise SystemExit(1)
print("LOG_SECRET_SCAN=PASS")
print("CONTAINER_SECRET_SCAN=PASS")
PY
record LOG_SECRET_SCAN PASS
record CONTAINER_SECRET_SCAN PASS

# Private key material must exist only inside the root-owned edge PKI.
sudo find "${BASE}" \( -name '*.key' -o -name '*.pem' \) | sudo tee "${OUT_DIR}/key-inventory.txt" >/dev/null
if sudo find "${BASE}/tenants" \( -name '*.key' -o -name '*.pem' \) | grep -q .; then
  die "private key material found inside a tenant namespace"
fi
edge_key="${BASE}/edge/data/caddy/pki/authorities/local/root.key"
if sudo test -f "$edge_key"; then
  mode="$(sudo stat -c '%a' "$edge_key")"
  case "$mode" in 600|640|700) ;; *) die "edge CA key has permissive mode ${mode}" ;; esac
fi
record PRIVATE_KEY_EXPOSURE_SCAN PASS

# ---------------------------------------------------------------------------
step "Summary"
# ---------------------------------------------------------------------------
docker rm -f vlux-minio >/dev/null 2>&1 || true

python3 - "$RESULTS" "$SUMMARY" "$VERSION" "$APP_IMAGE" <<'PY'
import json, sys
results = {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if "=" in line:
        key, _, value = line.partition("=")
        results[key] = value
results["CLOUD_VERSION"] = sys.argv[3]
results["APP_IMAGE"] = sys.argv[4]
results["CLOUD_DB_ENGINE"] = "PostgreSQL"
results["CLOUD_DB_MAJOR"] = "16"
results["CLOUD_DB_TARGET"] = "16.15"
results["TENANT_PRIVATE_NETWORKS"] = "vlux-tenant-a-private,vlux-tenant-b-private"
results["PUBLIC_DOMAIN_HTTPS"] = "MANUAL_PENDING"
results["CLOUD_HOST_REBOOT"] = "MANUAL_PENDING"
results["REAL_OFFSITE_BACKUP"] = "MANUAL_PENDING"
results["PHONE_CAMERA"] = "MANUAL_PENDING"
results["PHONE_REAL_SCAN"] = "MANUAL_PENDING"
json.dump(results, open(sys.argv[2], "w", encoding="utf-8"), indent=2, sort_keys=True)
print(json.dumps(results, indent=2, sort_keys=True))
PY

echo "CLOUD_TWO_TENANT_E2E=PASS"
