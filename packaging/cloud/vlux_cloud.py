from __future__ import annotations

import argparse
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def tenant_root(base: Path, tenant: str) -> Path:
    if not tenant.replace("-", "").replace("_", "").isalnum():
        fail("Tenant name may contain only letters, numbers, dash and underscore.")
    return base / "tenants" / tenant


def provision(args: argparse.Namespace) -> int:
    root = tenant_root(Path(args.base_dir), args.tenant)
    for path in ("config", "filestore", "logs", "postgres", "secrets", "backups"):
        (root / path).mkdir(parents=True, exist_ok=True)
    db_password = secrets.token_urlsafe(32)
    (root / "secrets" / "db_password").write_text(db_password + "\n", encoding="utf-8")
    metadata = {
        "tenant": args.tenant,
        "domain": args.domain,
        "edition": args.edition,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "isolation": {
            "database": f"vlux_{args.tenant}",
            "filestore": str(root / "filestore"),
            "secrets": str(root / "secrets"),
            "postgres_public": False,
        },
    }
    (root / "tenant.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "Caddyfile").write_text(
        f"{args.domain} {{\n  encode zstd gzip\n  reverse_proxy app:8069\n}}\n",
        encoding="utf-8",
    )
    print(f"Tenant provisioned: {args.tenant}. Secrets stored outside image at {root / 'secrets'}.")
    return 0


def backup(args: argparse.Namespace) -> int:
    root = tenant_root(Path(args.base_dir), args.tenant)
    if not root.exists():
        fail(f"Tenant not found: {args.tenant}")
    print(f"Backup plan ready for {args.tenant}: DB dump + filestore + tenant metadata.")
    return 0


def restore(args: argparse.Namespace) -> int:
    if args.confirm != "RESTORE_TENANT":
        fail("Restore requires --confirm RESTORE_TENANT")
    print(f"Restore preflight OK for tenant {args.tenant} from {args.backup}.")
    return 0


def upgrade(args: argparse.Namespace) -> int:
    print(f"Upgrade plan for {args.tenant}: backup, load image {args.release}, migrate target addons, health, smoke.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vlux-cloud")
    parser.add_argument("--base-dir", default=str(ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("provision")
    p.add_argument("tenant")
    p.add_argument("--domain", required=True)
    p.add_argument("--edition", choices=("local_complete", "cloud_managed"), default="cloud_managed")
    p.set_defaults(func=provision)

    p = sub.add_parser("backup")
    p.add_argument("tenant")
    p.set_defaults(func=backup)

    p = sub.add_parser("restore")
    p.add_argument("tenant")
    p.add_argument("backup")
    p.add_argument("--confirm", required=True)
    p.set_defaults(func=restore)

    p = sub.add_parser("upgrade")
    p.add_argument("tenant")
    p.add_argument("release")
    p.set_defaults(func=upgrade)
    return parser.parse_args()


if __name__ == "__main__":
    ns = parse_args()
    raise SystemExit(ns.func(ns))
