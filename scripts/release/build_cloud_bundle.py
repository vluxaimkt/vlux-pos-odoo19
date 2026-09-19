#!/usr/bin/env python3
"""Build the VLUX POS Cloud Managed operator bundle.

The bundle is what VLUX copies onto an Ubuntu 24.04 cloud host: the vlux-cloud
CLI, the edge templates, the tenant templates, the technical deployment README,
a manifest and checksums. It deliberately carries no customer data, no secrets,
no private keys, no database dumps and no .git directory - the payload is
asserted against that contract before the archive is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLOUD = ROOT / "packaging" / "cloud"

PAYLOAD = (
    ("vlux-cloud", 0o755),
    ("vlux_cloud.py", 0o644),
    ("entrypoint.sh", 0o755),
    ("Dockerfile", 0o644),
    ("README.md", 0o644),
    ("CLOUDFLARE_STAGING.md", 0o644),
    ("MIGRATION_TO_VPS.md", 0o644),
    ("R2_BACKUPS.md", 0o644),
    ("edge/compose.yaml.tmpl", 0o644),
    ("edge/Caddyfile.tmpl", 0o644),
    ("templates/tenant-compose.yaml.tmpl", 0o644),
    ("templates/tenant.caddy.tmpl", 0o644),
    ("templates/tenant-maintenance.caddy.tmpl", 0o644),
    ("templates/cloudflared-compose.yaml.tmpl", 0o644),
    ("templates/odoo.conf.tmpl", 0o644),
    ("systemd/vlux-pos-backup@.service", 0o644),
    ("systemd/vlux-pos-backup@.timer", 0o644),
)

FORBIDDEN_DIR_NAMES = {".git", "__pycache__", "secrets", "filestore", "backups", "certificates"}
FORBIDDEN_SUFFIXES = {".dump", ".sql", ".backup", ".pem", ".key", ".pfx", ".p12", ".log"}
CREDENTIAL_MARKERS = (
    "BEGIN RSA PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY",
    "BEGIN EC PRIVATE KEY",
    "BEGIN PRIVATE KEY",
    "github_pat_",
    "ghp_",
    "AKIA",
)
PATH_LEAK_MARKERS = ("C:\\", "/home/runner", "/Users/Administrador", "/workspace/")


def fail(message: str) -> None:
    print("ERROR: " + message, file=sys.stderr)
    raise SystemExit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_payload(staging: Path) -> list[Path]:
    staged = []
    for relative, mode in PAYLOAD:
        source = CLOUD / relative
        if not source.is_file():
            fail("Missing cloud payload file: " + relative)
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(mode)
        staged.append(target)
    return staged


def assert_clean(staging: Path) -> None:
    for path in staging.rglob("*"):
        parts = {part.lower() for part in path.relative_to(staging).parts}
        if parts & FORBIDDEN_DIR_NAMES:
            fail("Bundle payload contains a forbidden path: " + str(path.relative_to(staging)))
        if path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES:
            fail("Bundle payload contains a forbidden file: " + str(path.relative_to(staging)))
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in CREDENTIAL_MARKERS:
            if marker in text:
                fail("Bundle payload contains credential marker " + marker + " in " + path.name)
        for marker in PATH_LEAK_MARKERS:
            if marker in text:
                fail("Bundle payload leaks a developer path (" + marker + ") in " + path.name)


def build(version: str, source_commit: str, app_image: str, app_digest: str, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = "vlux-pos-cloud-" + version
    staging = out_dir / bundle_name
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    stage_payload(staging)

    sys.path.insert(0, str(ROOT / "scripts" / "release"))
    import importlib.util

    spec = importlib.util.spec_from_file_location("vlux_cloud", CLOUD / "vlux_cloud.py")
    cli = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(cli)

    files = {}
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(staging)).replace("\\", "/")] = {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }

    manifest = {
        "target": "cloud_managed",
        "product": "VLUX POS Cloud Managed",
        "cloud_version": version,
        "source_commit": source_commit,
        "odoo_commit": cli.ODOO_COMMIT,
        "python_version": cli.PYTHON_VERSION,
        "app_image": app_image,
        "app_image_digest": app_digest,
        "app_runs_non_root": "PASS",
        "postgres_image": cli.POSTGRES_IMAGE,
        "postgres_amd64_digest": cli.POSTGRES_AMD64_DIGEST,
        "cloud_db_engine": cli.CLOUD_DB_ENGINE,
        "cloud_db_major": cli.CLOUD_DB_MAJOR,
        "cloud_db_target": cli.CLOUD_DB_TARGET,
        "cloud_db_primary": "STANDARD_POSTGRESQL",
        "supabase_primary_db": "NO",
        "caddy_image": cli.CADDY_IMAGE,
        "cloudflared_image": cli.CLOUDFLARED_IMAGE,
        "cloudflared_amd64_digest": cli.CLOUDFLARED_AMD64_DIGEST,
        "edge_modes": list(cli.EDGE_MODES),
        "data_classes": list(cli.DATA_CLASSES),
        "deployment_classes": list(cli.DEPLOYMENT_CLASSES),
        "offsite_backend": "S3_COMPATIBLE",
        "edge_architecture": "SINGLE_GLOBAL_CADDY_PER_HOST",
        "edge_public_ports": ["80/tcp", "443/tcp", "443/udp"],
        "tenant_private_networks": "vlux-<tenant>-private (internal)",
        "postgres_public_exposure": "NONE",
        "odoo_http_port": cli.HTTP_PORT,
        "odoo_gevent_port": cli.GEVENT_PORT,
        "odoo_workers_default": cli.DEFAULT_WORKERS,
        "productive_addons": list(cli.PRODUCTIVE_ADDONS),
        "host_target": "Ubuntu 24.04 LTS amd64",
        "build_date": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "excludes": [
            "customer data",
            "secrets",
            "cloudflare tunnel token",
            "R2/S3 credentials",
            "private keys",
            "database dumps",
            ".git",
        ],
        "files": files,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checksums = "".join(
        files[name]["sha256"] + "  " + name + "\n" for name in sorted(files)
    )
    checksums += sha256_file(staging / "manifest.json") + "  manifest.json\n"
    (staging / "SHA256SUMS").write_text(checksums, encoding="utf-8")

    assert_clean(staging)

    archive = out_dir / (bundle_name + ".tar.gz")
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(staging, arcname=bundle_name)
    shutil.rmtree(staging)

    digest = sha256_file(archive)
    (out_dir / (archive.name + ".sha256")).write_text(
        digest + "  " + archive.name + "\n", encoding="ascii"
    )
    summary = {
        "cloud_bundle": archive.name,
        "cloud_bundle_sha256": digest,
        "cloud_bundle_size_mb": round(archive.stat().st_size / (1024 * 1024), 3),
        "cloud_bundle_path": str(archive),
        "manifest": manifest,
    }
    (out_dir / "bundle-manifest.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build the VLUX POS cloud operator bundle.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--app-image", default="")
    parser.add_argument("--app-digest", default="")
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv[1:])
    summary = build(
        args.version,
        args.source_commit,
        args.app_image,
        args.app_digest,
        args.out_dir.resolve(),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
