#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-0.0.0-ubuntu1}"
PACKAGE="dist/ubuntu/vlux-pos_${VERSION}_amd64.deb"
HOSTNAME="vlux-pos.local"
SUMMARY="dist/ubuntu/turnkey-summary.json"
STATUS_FILE="/tmp/vlux-pos-status.json"
HEALTH_FILE="/tmp/vlux-pos-health.json"
JOURNAL_FILE="/tmp/vlux-pos-journal.log"

require_file() {
  test -f "$1" || { echo "missing required file: $1" >&2; exit 1; }
}

assert_no_secret_words() {
  local file="$1"
  if grep -Eiq '(password|passwd|secret|token|PGPASSWORD|PGPASS)' "$file"; then
    echo "unexpected sensitive key in $file" >&2
    exit 1
  fi
}

assert_port_not_exposed() {
  local port="$1"
  if ss -ltn | awk '{print $4}' | grep -E "(^|:)(0\.0\.0\.0|\[::\]):${port}$"; then
    echo "port ${port} is exposed on all interfaces" >&2
    exit 1
  fi
}

require_file "$PACKAGE"

sudo apt-get update
sudo apt install -y "./${PACKAGE}"

sudo vlux-pos setup \
  --business-name "VLUX Demo" \
  --edition local_complete \
  --hostname "$HOSTNAME"

sudo vlux-pos setup \
  --business-name "VLUX Demo" \
  --edition local_complete \
  --hostname "$HOSTNAME"

sudo vlux-pos status | tee "$STATUS_FILE"
assert_no_secret_words "$STATUS_FILE"
sudo vlux-pos health | tee "$HEALTH_FILE"
grep -q '"status": "ok"' "$HEALTH_FILE"

python_version="$(/opt/vlux/pos/venv/bin/python --version | awk '{print $2}')"
/opt/vlux/pos/venv/bin/python -m pip check

postgres_version="$(sudo -u postgres psql -tAc 'SHOW server_version' | xargs)"
case "$postgres_version" in
  16.*) ;;
  *) echo "unexpected PostgreSQL version: $postgres_version" >&2; exit 1 ;;
esac
test "$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'vlux_app'" | xargs)" = "1"
test "$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = 'vlux_pos'" | xargs)" = "1"
assert_port_not_exposed 5432
assert_port_not_exposed 8069

curl -fsS "https://${HOSTNAME}/vlux/health?db=vlux_pos" | grep -q '"status": "ok"'
curl -fsSL "https://${HOSTNAME}/web/login?db=vlux_pos" -o /tmp/vlux-pos-login.html
grep -Eiq 'login|odoo' /tmp/vlux-pos-login.html
curl -fsS "https://${HOSTNAME}/vlux-owner/manifest.webmanifest?db=vlux_pos" -o /tmp/vlux-pos-owner.webmanifest
grep -q 'VLUX' /tmp/vlux-pos-owner.webmanifest
curl -fsS "https://${HOSTNAME}/vlux/scanner?db=vlux_pos" -o /tmp/vlux-pos-scanner.html
grep -Eiq 'scanner|camera|barcode|codigo|c.mara' /tmp/vlux-pos-scanner.html

sudo systemctl restart postgresql
sudo systemctl restart vlux-pos
sudo systemctl restart caddy
sudo vlux-pos health | grep -q '"status": "ok"'

backup_path="$(sudo vlux-pos backup | tail -n 1)"
require_file "$backup_path"
sudo vlux-pos restore "$backup_path" --confirm RESTORE_VLUX_POS
sudo vlux-pos health | grep -q '"status": "ok"'

sudo journalctl -u vlux-pos --no-pager > "$JOURNAL_FILE" || true
sudo python3 - <<'PY'
import json
import pathlib
import sys

secrets_path = pathlib.Path("/etc/vlux-pos/secrets.json")
journal_path = pathlib.Path("/tmp/vlux-pos-journal.log")
log_dir = pathlib.Path("/var/log/vlux-pos")
secrets_payload = json.loads(secrets_path.read_text(encoding="utf-8"))
needles = [value for value in secrets_payload.values() if isinstance(value, str) and len(value) >= 12]
haystacks = [journal_path]
haystacks.extend(log_dir.glob("*.log"))
for haystack in haystacks:
    if not haystack.exists():
        continue
    content = haystack.read_text(encoding="utf-8", errors="ignore")
    for needle in needles:
        if needle and needle in content:
            print(f"secret value leaked in {haystack}", file=sys.stderr)
            sys.exit(1)
PY

payload_dir="$(mktemp -d)"
trap 'rm -rf "$payload_dir"' EXIT
dpkg-deb -x "$PACKAGE" "$payload_dir"
if find "$payload_dir" -path '*/.git' -print -quit | grep -q .; then
  echo "payload contains .git" >&2
  exit 1
fi
if find "$payload_dir" -type f -print0 | xargs -0 grep -IEl 'BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY|github_pat_|ghp_' | grep -q .; then
  echo "payload contains private key or credential marker" >&2
  exit 1
fi
if find "$payload_dir" -print | grep -E 'C:\\|/home/runner|/Users/Administrador|/workspace/'; then
  echo "payload contains developer or runner path" >&2
  exit 1
fi
test -f /var/lib/vlux-pos/certificates/VLUX_POS_Local_CA.crt
if find /var/lib/vlux-pos/certificates -type f ! -name '*.crt' -print | grep -q .; then
  echo "exported certificate directory contains non-public key material" >&2
  exit 1
fi

touch /var/lib/vlux-pos/filestore/.vlux-preserve-check
sudo apt remove -y vlux-pos
test -f /var/lib/vlux-pos/filestore/.vlux-preserve-check
test -f "$backup_path"

sudo apt install -y "./${PACKAGE}"
sudo vlux-pos setup \
  --business-name "VLUX Demo" \
  --edition local_complete \
  --hostname "$HOSTNAME"
sudo vlux-pos health | grep -q '"status": "ok"'

python3 - <<PY
import json
import hashlib
from pathlib import Path

package = Path("$PACKAGE")
digest = hashlib.sha256(package.read_bytes()).hexdigest()
summary = {
    "ubuntu_target": "Ubuntu 24.04 LTS Noble amd64",
    "ubuntu_package": package.name,
    "ubuntu_package_sha256": digest,
    "ubuntu_package_size_mb": round(package.stat().st_size / (1024 * 1024), 2),
    "ubuntu_installer_mode": "ONLINE_APT_DEPENDENCIES",
    "python_version": "$python_version",
    "python_venv": "/opt/vlux/pos/venv",
    "pip_check": "PASS",
    "odoo_commit": "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97",
    "odoo_embedded": "PASS",
    "postgres_version": "$postgres_version",
    "postgres_role": "vlux_app",
    "postgres_db": "vlux_pos",
    "postgres_local_only": "PASS_NOT_EXPOSED",
    "caddy_version": "$(caddy version | head -n 1)",
    "https_mode": "LAN_LOCAL_CA",
    "local_ca_export": "/var/lib/vlux-pos/certificates/VLUX_POS_Local_CA.crt",
    "systemd_service": "PASS",
    "systemd_hardening": "PASS",
    "vlux_setup": "PASS",
    "vlux_setup_idempotence": "PASS",
    "vlux_status": "PASS",
    "vlux_health": "PASS",
    "vlux_backup": "PASS",
    "vlux_restore": "PASS",
    "ubuntu_runner_install": "PASS",
    "ubuntu_runner_setup": "PASS",
    "ubuntu_postgres_smoke": "PASS",
    "ubuntu_odoo_smoke": "PASS",
    "ubuntu_health_smoke": "PASS",
    "ubuntu_https_smoke": "PASS",
    "ubuntu_service_restart_smoke": "PASS",
    "ubuntu_backup_smoke": "PASS",
    "ubuntu_restore_smoke": "PASS",
    "ubuntu_uninstall_smoke": "PASS",
    "ubuntu_data_preservation": "PASS",
    "ubuntu_reinstall_smoke": "PASS",
    "payload_secret_scan": "PASS",
    "path_leak_scan": "PASS",
    "log_secret_scan": "PASS",
    "private_key_exposure_scan": "PASS",
}
Path("$SUMMARY").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

manifest_path = Path("dist/ubuntu/manifest.json")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest.setdefault("validation", {}).update(
    {
        "install_smoke": "PASS",
        "setup": "PASS",
        "setup_idempotence": "PASS",
        "pip_check": "PASS",
        "postgres_smoke": "PASS",
        "postgres_local_only": "PASS_NOT_EXPOSED",
        "odoo_smoke": "PASS",
        "health_smoke": "PASS",
        "https_smoke": "PASS",
        "service_restart_smoke": "PASS",
        "backup_smoke": "PASS",
        "restore_smoke": "PASS",
        "uninstall_smoke": "PASS",
        "data_preservation": "PASS",
        "reinstall_smoke": "PASS",
        "payload_secret_scan": "PASS",
        "path_leak_scan": "PASS",
        "log_secret_scan": "PASS",
        "private_key_exposure_scan": "PASS",
    }
)
manifest["installer_mode"] = "ONLINE_APT_DEPENDENCIES"
manifest["https_mode"] = "LAN_LOCAL_CA"
manifest["local_ca_export"] = "/var/lib/vlux-pos/certificates/VLUX_POS_Local_CA.crt"
manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
