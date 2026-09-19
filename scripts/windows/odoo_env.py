from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def load_odoo(odoo_core: str):
    sys.path.insert(0, odoo_core)
    from odoo.tools import config

    return config


def resolve(odoo_core: str, odoo_conf: str, db_name: str) -> dict:
    config = load_odoo(odoo_core)
    config.parse_config(["-c", odoo_conf, "-d", db_name])
    data_dir = Path(config["data_dir"]).resolve()
    filestore = Path(config.filestore(db_name)).resolve()
    return {
        "configured_data_dir": str(data_dir),
        "database": db_name,
        "db_host": str(config.get("db_host") or "localhost"),
        "db_port": str(config.get("db_port") or "5432"),
        "db_user": str(config.get("db_user") or ""),
        "expected_filestore": str(filestore),
        "filestore_root": str(filestore.parent),
        "filestore_exists": filestore.is_dir(),
        "filestore_has_content": filestore.is_dir() and any(filestore.rglob("*")),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_backup(backup_dir: Path, db_name: str) -> int:
    manifest = backup_dir / "backup-manifest.json"
    dump = backup_dir / "database.dump"
    checksums = backup_dir / "checksums.txt"
    filestore = backup_dir / "filestore" / db_name
    invalid_marker = backup_dir / "INVALID_BACKUP.txt"

    errors = []
    if invalid_marker.exists():
        errors.append("backup is marked invalid")
    if not manifest.is_file():
        errors.append("backup-manifest.json missing")
    if not dump.is_file() or dump.stat().st_size <= 0:
        errors.append("database.dump missing or empty")
    if not checksums.is_file():
        errors.append("checksums.txt missing")
    if not filestore.is_dir():
        errors.append("filestore copy missing")

    if not errors and manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if payload.get("db") != db_name:
                errors.append("manifest db mismatch")
            if Path(payload.get("database_dump", "")).name != "database.dump":
                errors.append("manifest database_dump invalid")
        except Exception as exc:
            errors.append(f"manifest invalid: {exc}")

    if not errors and checksums.is_file():
        expected = checksums.read_text(encoding="ascii").split()[0].lower()
        actual = sha256(dump)
        if actual != expected:
            errors.append("database.dump checksum mismatch")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Backup validation OK.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    env_parser = subparsers.add_parser("env")
    env_parser.add_argument("--odoo-core", required=True)
    env_parser.add_argument("--config", required=True)
    env_parser.add_argument("--db", required=True)
    env_parser.add_argument("--format", choices=["json", "bat"], default="json")

    backup_parser = subparsers.add_parser("validate-backup")
    backup_parser.add_argument("--backup-dir", required=True)
    backup_parser.add_argument("--db", required=True)

    args = parser.parse_args(argv)
    if args.command == "env":
        payload = resolve(args.odoo_core, args.config, args.db)
        if args.format == "bat":
            print(f'set "CONFIGURED_DATA_DIR={payload["configured_data_dir"]}"')
            print(f'set "ODOO_DB_HOST={payload["db_host"]}"')
            print(f'set "ODOO_DB_PORT={payload["db_port"]}"')
            print(f'set "ODOO_DB_USER={payload["db_user"]}"')
            print(f'set "EXPECTED_FILESTORE={payload["expected_filestore"]}"')
            print(f'set "FILESTORE_ROOT={payload["filestore_root"]}"')
            print(f'set "FILESTORE_EXISTS={int(payload["filestore_exists"])}"')
            print(f'set "FILESTORE_HAS_CONTENT={int(payload["filestore_has_content"])}"')
        else:
            print(json.dumps(payload, indent=2))
        return 0
    if args.command == "validate-backup":
        return validate_backup(Path(args.backup_dir), args.db)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
