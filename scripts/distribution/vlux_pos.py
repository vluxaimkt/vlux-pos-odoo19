from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path


PRODUCTIVE_EDITIONS = {
    "local_core": ("vlux_core",),
    "local_complete": ("vlux_core", "vlux_mobile_scanner", "vlux_owner"),
    "cloud_managed": ("vlux_core", "vlux_mobile_scanner", "vlux_owner"),
}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def run(args: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, env=env, check=check, text=True)


class VluxLayout:
    def __init__(self, root: Path, etc: Path, data: Path, backups: Path, logs: Path):
        self.root = root
        self.etc = etc
        self.data = data
        self.backups = backups
        self.logs = logs
        self.filestore = data / "filestore"
        self.config = etc / "odoo.conf"
        self.secrets = etc / "secrets.json"
        self.release_manifest = root / "release-manifest.json"

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
        for path in (self.root, self.etc, self.data, self.backups, self.logs, self.filestore):
            path.mkdir(parents=True, exist_ok=True)


def load_manifest(layout: VluxLayout) -> dict:
    if not layout.release_manifest.exists():
        return {}
    return json.loads(layout.release_manifest.read_text(encoding="utf-8"))


def write_secret_file(layout: VluxLayout, *, db_user: str, db_password: str, admin_password: str) -> None:
    payload = {
        "db_user": db_user,
        "db_password": db_password,
        "admin_passwd": admin_password,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    layout.secrets.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(layout.secrets, 0o600)
    except PermissionError:
        pass


def render_odoo_conf(layout: VluxLayout, *, db_host: str, db_port: str, db_user: str, http_port: str) -> None:
    config = f"""[options]
admin_passwd = __VLUX_ADMIN_PASSWORD_FROM_SECRET_FILE__
db_host = {db_host}
db_port = {db_port}
db_user = {db_user}
db_password = __VLUX_DB_PASSWORD_FROM_SECRET_FILE__
addons_path = {layout.root / "odoo" / "odoo" / "addons"},{layout.root / "odoo" / "addons"},{layout.root / "addons"}
data_dir = {layout.data}
logfile = {layout.logs / "odoo.log"}
http_port = {http_port}
proxy_mode = True
list_db = False
"""
    layout.config.write_text(config, encoding="utf-8")
    try:
        os.chmod(layout.config, 0o640)
    except PermissionError:
        pass


def setup(args: argparse.Namespace) -> int:
    layout = VluxLayout.linux()
    if args.edition not in PRODUCTIVE_EDITIONS:
        fail(f"Unsupported edition: {args.edition}")
    layout.ensure_dirs()
    db_user = args.db_user or "vlux_pos"
    db_password = args.db_password or secrets.token_urlsafe(32)
    admin_password = args.admin_password or secrets.token_urlsafe(32)
    write_secret_file(layout, db_user=db_user, db_password=db_password, admin_password=admin_password)
    render_odoo_conf(
        layout,
        db_host=args.db_host,
        db_port=args.db_port,
        db_user=db_user,
        http_port=args.http_port,
    )
    setup_state = {
        "business_name": args.business_name,
        "edition": args.edition,
        "hostname": args.hostname,
        "addons": list(PRODUCTIVE_EDITIONS[args.edition]),
        "configured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    (layout.etc / "setup.json").write_text(
        json.dumps(setup_state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"VLUX POS configured for {args.edition}. Secrets stored at {layout.secrets}.")
    return 0


def smoke(args: argparse.Namespace) -> int:
    layout = VluxLayout.linux()
    manifest = load_manifest(layout)
    expected = PRODUCTIVE_EDITIONS.get(args.edition or manifest.get("edition", "local_complete"))
    if not expected:
        fail("Unknown edition for smoke check.")
    if not layout.config.exists():
        fail(f"Missing config: {layout.config}")
    if not layout.secrets.exists():
        fail(f"Missing secrets file: {layout.secrets}")
    if args.odoo_bin:
        modules = ",".join(expected)
        run(
            [
                sys.executable,
                args.odoo_bin,
                "-c",
                str(layout.config),
                "-d",
                args.database,
                "-u",
                modules,
                "--stop-after-init",
            ]
        )
    print("Smoke OK: " + ", ".join(expected))
    return 0


def backup(args: argparse.Namespace) -> int:
    layout = VluxLayout.linux()
    layout.ensure_dirs()
    stamp = utc_stamp()
    backup_dir = layout.backups / f"vlux-pos-{stamp}"
    backup_dir.mkdir(parents=True)
    manifest = load_manifest(layout)
    (backup_dir / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.pg_dump:
        dump_path = backup_dir / "database.dump"
        run([args.pg_dump, "--format=custom", "--file", str(dump_path), args.database])
    if layout.filestore.exists():
        with tarfile.open(backup_dir / "filestore.tar.gz", "w:gz") as archive:
            archive.add(layout.filestore, arcname="filestore")
    with tarfile.open(layout.backups / f"{backup_dir.name}.tar.gz", "w:gz") as archive:
        archive.add(backup_dir, arcname=backup_dir.name)
    shutil.rmtree(backup_dir)
    print(f"Backup created: {layout.backups / (backup_dir.name + '.tar.gz')}")
    return 0


def restore(args: argparse.Namespace) -> int:
    if args.confirm != "RESTORE_VLUX_POS":
        fail("Restore requires --confirm RESTORE_VLUX_POS", code=2)
    backup_path = Path(args.backup).resolve()
    if not backup_path.is_file():
        fail(f"Backup not found: {backup_path}")
    print(f"Restore preflight OK for {backup_path}. Apply DB/filestore restore in maintenance window.")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vlux-pos")
    sub = parser.add_subparsers(dest="command", required=True)

    setup_parser = sub.add_parser("setup")
    setup_parser.add_argument("--business-name", required=True)
    setup_parser.add_argument("--edition", choices=sorted(PRODUCTIVE_EDITIONS), default="local_complete")
    setup_parser.add_argument("--hostname", required=True)
    setup_parser.add_argument("--db-host", default="localhost")
    setup_parser.add_argument("--db-port", default="5432")
    setup_parser.add_argument("--db-user")
    setup_parser.add_argument("--db-password")
    setup_parser.add_argument("--admin-password")
    setup_parser.add_argument("--http-port", default="8069")
    setup_parser.set_defaults(func=setup)

    smoke_parser = sub.add_parser("smoke")
    smoke_parser.add_argument("--edition")
    smoke_parser.add_argument("--odoo-bin")
    smoke_parser.add_argument("--database", default="vlux_pos")
    smoke_parser.set_defaults(func=smoke)

    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--database", default="vlux_pos")
    backup_parser.add_argument("--pg-dump", default=shutil.which("pg_dump") or "")
    backup_parser.set_defaults(func=backup)

    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("backup")
    restore_parser.add_argument("--confirm", required=True)
    restore_parser.set_defaults(func=restore)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
