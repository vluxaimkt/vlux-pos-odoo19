from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PRODUCTIVE_EDITIONS = {
    "local_core": ("vlux_core",),
    "local_complete": ("vlux_core", "vlux_mobile_scanner", "vlux_owner", "vlux_pos_catalog"),
    "cloud_managed": ("vlux_core", "vlux_mobile_scanner", "vlux_owner", "vlux_pos_catalog"),
}
DB_NAME = "vlux_pos"
DB_ROLE = "vlux_app"
DB_PORT = "5432"
HTTP_HOST = "127.0.0.1"
HTTP_PORT = "8069"
LOCAL_CA_EXPORT = Path("/var/lib/vlux-pos/certificates/VLUX_POS_Local_CA.crt")
ODOO_COMMIT = "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97"


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    check: bool = True,
    capture: bool = False,
    user: str | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = args
    if user:
        quoted = " ".join(shlex_quote(part) for part in args)
        cmd = ["runuser", "-u", user, "--", "sh", "-c", quoted]
    return subprocess.run(
        cmd,
        check=check,
        env=env,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def shlex_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


class VluxLayout:
    def __init__(self, root: Path, etc: Path, data: Path, backups: Path, logs: Path):
        self.root = root
        self.etc = etc
        self.data = data
        self.backups = backups
        self.logs = logs
        self.filestore = data / "filestore"
        self.certificates = data / "certificates"
        self.config = etc / "odoo.conf"
        self.caddy_dir = etc / "caddy"
        self.caddyfile = self.caddy_dir / "Caddyfile"
        self.secrets = etc / "secrets.json"
        self.setup_state = etc / "setup.json"
        self.release_manifest = root / "release-manifest.json"
        self.odoo_dir = root / "odoo"
        self.odoo_bin = self.odoo_dir / "odoo-bin"
        self.odoo_commit = self.odoo_dir / "ODOO_COMMIT.txt"
        self.venv = root / "venv"

    @classmethod
    def linux(cls) -> "VluxLayout":
        return cls(
            root=Path(os.environ.get("VLUX_POS_ROOT", "/opt/vlux/pos")),
            etc=Path(os.environ.get("VLUX_POS_CONFIG", "/etc/vlux-pos")),
            data=Path(os.environ.get("VLUX_POS_DATA", "/var/lib/vlux-pos")),
            backups=Path(os.environ.get("VLUX_POS_BACKUPS", "/var/backups/vlux-pos")),
            logs=Path(os.environ.get("VLUX_POS_LOGS", "/var/log/vlux-pos")),
        )

    def ensure_dirs(self) -> None:
        for path in (
            self.root,
            self.etc,
            self.caddy_dir,
            self.data,
            self.filestore,
            self.certificates,
            self.backups,
            self.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(path, mode)
    except PermissionError:
        pass


def require_root() -> None:
    if os.name == "posix" and os.geteuid() != 0:
        fail("This command must be run as root. Use sudo.")


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        fail(f"Missing {label}: {path}")


def read_release_manifest(layout: VluxLayout) -> dict:
    return load_json(layout.release_manifest)


def read_secrets(layout: VluxLayout) -> dict:
    payload = load_json(layout.secrets)
    missing = [key for key in ("db_password", "admin_passwd", "initial_admin_password") if not payload.get(key)]
    if missing:
        fail(f"Secrets file is incomplete: {layout.secrets}")
    return payload


def ensure_secrets(layout: VluxLayout) -> dict:
    current = load_json(layout.secrets)
    if current.get("db_password") and current.get("admin_passwd") and current.get("initial_admin_password"):
        return current
    payload = {
        "db_user": DB_ROLE,
        "db_password": current.get("db_password") or secrets.token_urlsafe(36),
        "admin_passwd": current.get("admin_passwd") or secrets.token_urlsafe(36),
        "initial_admin_login": current.get("initial_admin_login") or "admin",
        "initial_admin_password": current.get("initial_admin_password") or secrets.token_urlsafe(36),
        "created_at": current.get("created_at")
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    write_json(layout.secrets, payload, mode=0o640)
    run(["chown", "root:vlux-pos", str(layout.secrets)], check=False)
    return payload


def local_ipv4_addresses() -> list[str]:
    try:
        output = run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture=True).stdout or ""
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    addresses: list[str] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[1].startswith(("docker", "br-", "veth")):
            continue
        address = parts[3].split("/", 1)[0]
        if address.startswith(("127.", "169.254.")):
            continue
        addresses.append(address)
    return addresses


def hostname_is_public(hostname: str) -> bool:
    if hostname.endswith(".local") or hostname in {"localhost", socket.gethostname()}:
        return False
    try:
        return any(not item[4][0].startswith(("127.", "10.", "192.168.", "172.16.")) for item in socket.getaddrinfo(hostname, None))
    except socket.gaierror:
        return False


def ensure_hosts_entry(hostname: str) -> None:
    if hostname_is_public(hostname):
        return
    hosts = Path("/etc/hosts")
    current = hosts.read_text(encoding="utf-8") if hosts.exists() else ""
    if hostname in current:
        return
    target = local_ipv4_addresses()[0] if local_ipv4_addresses() else "127.0.0.1"
    with hosts.open("a", encoding="utf-8") as stream:
        stream.write(f"\n{target} {hostname}\n")


def configure_postgres() -> None:
    run(["systemctl", "enable", "--now", "postgresql"], check=False)
    conf_d = Path("/etc/postgresql/16/main/conf.d")
    if conf_d.exists():
        local_conf = conf_d / "vlux-pos-local.conf"
        local_conf.write_text("listen_addresses = 'localhost'\nport = 5432\n", encoding="utf-8")
        run(["systemctl", "restart", "postgresql"])
    wait_postgres(timeout=90)


def postgres_sql(sql: str) -> str:
    return run(["psql", "-v", "ON_ERROR_STOP=1", "-tAc", sql], user="postgres", capture=True).stdout.strip()


def configure_database(layout: VluxLayout, secret_payload: dict) -> None:
    escaped_password = secret_payload["db_password"].replace("'", "''")
    role_exists = postgres_sql(f"SELECT 1 FROM pg_roles WHERE rolname = '{DB_ROLE}'")
    if role_exists != "1":
        postgres_sql(f"CREATE ROLE {DB_ROLE} LOGIN PASSWORD '{escaped_password}'")
    else:
        postgres_sql(f"ALTER ROLE {DB_ROLE} WITH LOGIN PASSWORD '{escaped_password}'")
    db_exists = postgres_sql(f"SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}'")
    if db_exists != "1":
        postgres_sql(f"CREATE DATABASE {DB_NAME} OWNER {DB_ROLE} TEMPLATE template0 ENCODING 'UTF8'")
    postgres_sql(f"ALTER DATABASE {DB_NAME} OWNER TO {DB_ROLE}")
    run(["chown", "-R", "vlux-pos:vlux-pos", str(layout.data), str(layout.logs), str(layout.backups)])


def wait_postgres(timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run(["pg_isready", "-h", "127.0.0.1", "-p", DB_PORT], check=False, capture=True)
        if result.returncode == 0:
            return
        time.sleep(2)
    fail("PostgreSQL did not become ready in time")


def verify_postgres_local_only() -> None:
    output = run(["ss", "-ltn"], capture=True).stdout or ""
    exposed = []
    for line in output.splitlines():
        if f":{DB_PORT} " not in line:
            continue
        local = line.split()[3]
        if local.startswith(("0.0.0.0:", "[::]:")):
            exposed.append(local)
    if exposed:
        fail(f"PostgreSQL is exposed beyond localhost: {', '.join(exposed)}")


def ensure_venv(layout: VluxLayout) -> None:
    if not layout.venv.exists():
        run(["python3", "-m", "venv", str(layout.venv)])
    pip = layout.venv / "bin" / "pip"
    run([str(pip), "install", "--disable-pip-version-check", "pip==24.3.1", "setuptools==75.8.0", "wheel==0.45.1"])
    reqs = [layout.odoo_dir / "requirements.txt", layout.root / "requirements-extra.txt"]
    for req in reqs:
        if req.exists():
            run([str(pip), "install", "--disable-pip-version-check", "-r", str(req)])
    run([str(layout.venv / "bin" / "python"), "-m", "pip", "check"])
    run(["chown", "-R", "root:root", str(layout.venv)], check=False)


def render_odoo_conf(layout: VluxLayout, secret_payload: dict) -> None:
    addons_path = ",".join(
        str(path)
        for path in (
            layout.odoo_dir / "odoo" / "addons",
            layout.odoo_dir / "addons",
            layout.root / "addons",
        )
    )
    config = f"""[options]
admin_passwd = {secret_payload["admin_passwd"]}
db_host = 127.0.0.1
db_port = {DB_PORT}
db_user = {DB_ROLE}
db_password = {secret_payload["db_password"]}
db_name = {DB_NAME}
dbfilter = ^{DB_NAME}$
addons_path = {addons_path}
data_dir = {layout.data}
logfile = {layout.logs / "odoo.log"}
http_interface = {HTTP_HOST}
http_port = {HTTP_PORT}
proxy_mode = True
list_db = False
without_demo = True
"""
    layout.config.write_text(config, encoding="utf-8")
    os.chmod(layout.config, 0o640)
    run(["chown", "root:vlux-pos", str(layout.config)], check=False)


def verify_odoo_payload(layout: VluxLayout) -> None:
    require_file(layout.odoo_bin, "Odoo executable")
    require_file(layout.odoo_commit, "Odoo commit marker")
    commit = layout.odoo_commit.read_text(encoding="utf-8").strip()
    if commit != ODOO_COMMIT:
        fail(f"Unexpected Odoo payload commit: {commit}")
    if (layout.odoo_dir / ".git").exists():
        fail("Odoo payload must not contain .git")


def odoo_run(layout: VluxLayout, args: list[str]) -> None:
    command = [
        str(layout.venv / "bin" / "python"),
        str(layout.odoo_bin),
        "-c",
        str(layout.config),
        "-d",
        DB_NAME,
        *args,
    ]
    run(command, user="vlux-pos")


def install_addons(layout: VluxLayout, edition: str) -> None:
    modules = ",".join(PRODUCTIVE_EDITIONS[edition])
    odoo_run(layout, ["-i", modules, "-u", modules, "--stop-after-init", "--without-demo=all"])


def set_admin_password(layout: VluxLayout, secret_payload: dict) -> None:
    script = (
        "user = env.ref('base.user_admin')\n"
        f"user.sudo().write({{'login': 'admin', 'password': {secret_payload['initial_admin_password']!r}}})\n"
        "env.cr.commit()\n"
    )
    run(
        [
            str(layout.venv / "bin" / "python"),
            str(layout.odoo_bin),
            "shell",
            "-c",
            str(layout.config),
            "-d",
            DB_NAME,
        ],
        input_text=script,
        user="vlux-pos",
    )


def configure_caddy(layout: VluxLayout, hostname: str) -> str:
    public = hostname_is_public(hostname)
    https_mode = "PUBLIC_DOMAIN" if public else "LAN_LOCAL_CA"
    extra_hosts = [] if public else [socket.gethostname(), "localhost", "127.0.0.1", *local_ipv4_addresses()]
    names = [hostname] + [item for item in extra_hosts if item and item != hostname]
    sites = ", ".join(dict.fromkeys(names))
    tls_line = "" if public else "    tls internal\n"
    layout.caddyfile.write_text(
        f"""{sites} {{
{tls_line}    encode zstd gzip
    header {{
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy strict-origin-when-cross-origin
    }}
    reverse_proxy 127.0.0.1:{HTTP_PORT}
}}
""",
        encoding="utf-8",
    )
    os.chmod(layout.caddyfile, 0o644)
    run(["cp", str(layout.caddyfile), "/etc/caddy/Caddyfile"])
    ensure_hosts_entry(hostname)
    run(["systemctl", "enable", "--now", "caddy"], check=False)
    run(["systemctl", "restart", "caddy"])
    if not public:
        export_local_ca(layout)
    return https_mode


def export_local_ca(layout: VluxLayout) -> None:
    source = Path("/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt")
    for _ in range(30):
        if source.exists():
            break
        try:
            urllib.request.urlopen(
                f"https://127.0.0.1/vlux/health?db={DB_NAME}",
                context=ssl._create_unverified_context(),
                timeout=3,
            ).read()
        except (urllib.error.URLError, TimeoutError, ssl.SSLError):
            pass
        time.sleep(1)
    require_file(source, "Caddy local CA root")
    LOCAL_CA_EXPORT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, LOCAL_CA_EXPORT)
    os.chmod(LOCAL_CA_EXPORT, 0o644)
    trust_target = Path("/usr/local/share/ca-certificates/VLUX_POS_Local_CA.crt")
    shutil.copy2(source, trust_target)
    run(["update-ca-certificates"])


def wait_health(hostname: str, timeout: int = 120) -> str:
    url = f"https://{hostname}/vlux/health?db={DB_NAME}"
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status == 200:
                    return body
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            last_error = str(exc)
        time.sleep(3)
    fail(f"Health endpoint did not return HTTP 200 at {url}: {last_error}")


def setup(args: argparse.Namespace) -> int:
    require_root()
    if args.edition not in PRODUCTIVE_EDITIONS:
        fail(f"Unsupported edition: {args.edition}")
    layout = VluxLayout.linux()
    layout.ensure_dirs()
    verify_odoo_payload(layout)
    secret_payload = ensure_secrets(layout)
    configure_postgres()
    configure_database(layout, secret_payload)
    verify_postgres_local_only()
    ensure_venv(layout)
    render_odoo_conf(layout, secret_payload)
    install_addons(layout, args.edition)
    set_admin_password(layout, secret_payload)
    run(["systemctl", "daemon-reload"], check=False)
    run(["systemctl", "enable", "--now", "vlux-pos"])
    https_mode = configure_caddy(layout, args.hostname)
    wait_health(args.hostname)
    state = {
        "business_name": args.business_name,
        "edition": args.edition,
        "hostname": args.hostname,
        "https_mode": https_mode,
        "url": f"https://{args.hostname}",
        "database": DB_NAME,
        "db_role": DB_ROLE,
        "addons": list(PRODUCTIVE_EDITIONS[args.edition]),
        "odoo_commit": ODOO_COMMIT,
        "local_ca_export": str(LOCAL_CA_EXPORT) if https_mode == "LAN_LOCAL_CA" else None,
        "configured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    write_json(layout.setup_state, state, mode=0o640)
    print(json.dumps({key: value for key, value in state.items() if key != "addons"} | {"addons": state["addons"]}, indent=2))
    return 0


def wait_db(_: argparse.Namespace) -> int:
    wait_postgres(timeout=90)
    return 0


def health(_: argparse.Namespace) -> int:
    layout = VluxLayout.linux()
    state = load_json(layout.setup_state)
    hostname = state.get("hostname") or "localhost"
    body = wait_health(hostname, timeout=30)
    print(body)
    return 0


def status(_: argparse.Namespace) -> int:
    layout = VluxLayout.linux()
    manifest = read_release_manifest(layout)
    setup_state = load_json(layout.setup_state)
    postgres_status = run(["systemctl", "is-active", "postgresql"], check=False, capture=True).stdout.strip()
    odoo_status = run(["systemctl", "is-active", "vlux-pos"], check=False, capture=True).stdout.strip()
    caddy_status = run(["systemctl", "is-active", "caddy"], check=False, capture=True).stdout.strip()
    health_status = "UNKNOWN"
    if setup_state.get("hostname"):
        try:
            wait_health(setup_state["hostname"], timeout=10)
            health_status = "PASS"
        except SystemExit:
            health_status = "FAIL"
    payload = {
        "product": "VLUX POS",
        "version": manifest.get("version"),
        "edition": setup_state.get("edition") or manifest.get("edition"),
        "odoo_commit": ODOO_COMMIT,
        "postgresql": postgres_status,
        "odoo": odoo_status,
        "caddy": caddy_status,
        "health": health_status,
        "url": setup_state.get("url"),
        "database": DB_NAME,
        "db_role": DB_ROLE,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def backup(_: argparse.Namespace) -> int:
    require_root()
    layout = VluxLayout.linux()
    secrets_payload = read_secrets(layout)
    stamp = utc_stamp()
    staging = layout.backups / f"vlux-pos-{stamp}"
    staging.mkdir(parents=True, mode=0o750, exist_ok=False)
    db_dump = staging / "database.dump"
    env = os.environ.copy()
    env["PGPASSWORD"] = secrets_payload["db_password"]
    run(["pg_dump", "-h", "127.0.0.1", "-p", DB_PORT, "-U", DB_ROLE, "--format=custom", "--file", str(db_dump), DB_NAME], env=env)
    if layout.filestore.exists():
        with tarfile.open(staging / "filestore.tar.gz", "w:gz") as archive:
            archive.add(layout.filestore, arcname="filestore")
    metadata = {
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "database": DB_NAME,
        "db_role": DB_ROLE,
        "odoo_commit": ODOO_COMMIT,
        "release_manifest": read_release_manifest(layout),
    }
    write_json(staging / "manifest.json", metadata, mode=0o640)
    archive_path = layout.backups / f"{staging.name}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(staging, arcname=staging.name)
    shutil.rmtree(staging)
    os.chmod(archive_path, 0o640)
    print(str(archive_path))
    return 0


def safe_extract(archive: tarfile.TarFile, target: Path) -> None:
    root = target.resolve()
    for member in archive.getmembers():
        member_path = (target / member.name).resolve()
        if not str(member_path).startswith(str(root)):
            fail(f"Unsafe path in backup archive: {member.name}")
    archive.extractall(target)


def restore(args: argparse.Namespace) -> int:
    require_root()
    if args.confirm != "RESTORE_VLUX_POS":
        fail("Restore requires --confirm RESTORE_VLUX_POS", code=2)
    layout = VluxLayout.linux()
    backup_path = Path(args.backup).resolve()
    require_file(backup_path, "backup archive")
    secrets_payload = read_secrets(layout)
    temp_dir = layout.backups / f"_restore-{utc_stamp()}"
    temp_dir.mkdir(parents=True, mode=0o750)
    try:
        with tarfile.open(backup_path, "r:gz") as archive:
            safe_extract(archive, temp_dir)
        roots = [path for path in temp_dir.iterdir() if path.is_dir()]
        if len(roots) != 1:
            fail("Backup archive must contain exactly one root directory")
        backup_root = roots[0]
        require_file(backup_root / "database.dump", "database dump")
        run(["systemctl", "stop", "vlux-pos"], check=False)
        postgres_sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{DB_NAME}'")
        postgres_sql(f"DROP DATABASE IF EXISTS {DB_NAME}")
        postgres_sql(f"CREATE DATABASE {DB_NAME} OWNER {DB_ROLE} TEMPLATE template0 ENCODING 'UTF8'")
        env = os.environ.copy()
        env["PGPASSWORD"] = secrets_payload["db_password"]
        run(["pg_restore", "-h", "127.0.0.1", "-p", DB_PORT, "-U", DB_ROLE, "-d", DB_NAME, str(backup_root / "database.dump")], env=env)
        filestore_archive = backup_root / "filestore.tar.gz"
        if filestore_archive.exists():
            if layout.filestore.exists():
                shutil.rmtree(layout.filestore)
            layout.filestore.mkdir(parents=True, exist_ok=True)
            with tarfile.open(filestore_archive, "r:gz") as archive:
                safe_extract(archive, layout.data)
        run(["chown", "-R", "vlux-pos:vlux-pos", str(layout.data)])
        run(["systemctl", "start", "vlux-pos"])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    print(f"Restore completed: {backup_path}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vlux-pos")
    sub = parser.add_subparsers(dest="command", required=True)

    setup_parser = sub.add_parser("setup")
    setup_parser.add_argument("--business-name", required=True)
    setup_parser.add_argument("--edition", choices=sorted(PRODUCTIVE_EDITIONS), default="local_complete")
    setup_parser.add_argument("--hostname", required=True)
    setup_parser.set_defaults(func=setup)

    wait_db_parser = sub.add_parser("wait-db")
    wait_db_parser.add_argument("--quiet", action="store_true")
    wait_db_parser.set_defaults(func=wait_db)

    status_parser = sub.add_parser("status")
    status_parser.set_defaults(func=status)

    health_parser = sub.add_parser("health")
    health_parser.set_defaults(func=health)

    backup_parser = sub.add_parser("backup")
    backup_parser.set_defaults(func=backup)

    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("backup")
    restore_parser.add_argument("--confirm", required=True)
    restore_parser.set_defaults(func=restore)

    logs_parser = sub.add_parser("logs")
    logs_parser.add_argument("-n", "--lines", default="100")
    logs_parser.set_defaults(func=lambda args: run(["journalctl", "-u", "vlux-pos", "-n", args.lines, "--no-pager"]).returncode)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
