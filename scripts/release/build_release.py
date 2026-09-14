from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADDONS = ("vlux_core", "vlux_mobile_scanner", "vlux_owner", "vlux_facturacion")
ODOO_COMMIT = "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97"
PYTHON_VERSION = "3.12.10"
SUPPORTED_POSTGRESQL = "16.14"
PRODUCT = "VLUX POS"

EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "coverage",
    "htmlcov",
    "backups",
    "filestore",
    "secrets",
    "certificates",
    "release",
    "releases",
    "release_output",
    "_release_output",
}
EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".bak",
    ".tmp",
    ".dump",
    ".backup",
    ".sql",
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".zip",
}
EXCLUDE_NAMES = {
    ".env",
    "odoo.conf",
    "pgpass.conf",
}
EXCLUDE_PREFIXES = ("e2e_",)


def run(args: list[str], cwd: Path = ROOT) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, encoding="utf-8").strip()


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def ensure_clean_tree() -> None:
    status = run(["git", "status", "--porcelain"])
    if status:
        fail("Working tree is not clean. Commit or stash changes before building a release.")


def git_sha(path: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], cwd=path)


def validate_odoo_baseline() -> None:
    odoo_home = Path(os.environ.get("ODOO_HOME", r"C:\Odoo\src\odoo"))
    if not (odoo_home / ".git").exists():
        fail(f"ODOO_HOME is not a git checkout: {odoo_home}")
    current = git_sha(odoo_home)
    if current != ODOO_COMMIT:
        fail(f"Unexpected Odoo commit {current}; expected {ODOO_COMMIT}")


def read_manifest(addon: str) -> dict:
    path = ROOT / addon / "__manifest__.py"
    try:
        return ast.literal_eval(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Cannot parse {path}: {exc}")


def should_include(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if parts.intersection(EXCLUDE_DIRS):
        return False
    if path.name.lower() in EXCLUDE_NAMES:
        return False
    if path.name.lower().startswith(EXCLUDE_PREFIXES):
        return False
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    return True


def copy_tree(src: Path, dst: Path) -> None:
    for path in src.rglob("*"):
        if not should_include(path.relative_to(src)):
            continue
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_dir(src: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(src).as_posix())


def build(version: str) -> Path:
    if not version:
        fail("Usage: BUILD_RELEASE.bat <version>")
    if os.environ.get("VLUX_ALLOW_DIRTY_BUILD") != "1":
        ensure_clean_tree()
    validate_odoo_baseline()

    source_commit = git_sha(ROOT)
    addon_versions = {addon: read_manifest(addon).get("version") for addon in ADDONS}
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    out_root = ROOT.parent / "vlux_release_output" / version
    staging = out_root / "staging"
    package_name = f"VLUX_POS_{version}.zip"
    package_path = out_root / package_name

    if out_root.exists():
        shutil.rmtree(out_root)
    staging.mkdir(parents=True)

    for addon in ADDONS:
        copy_tree(ROOT / addon, staging / "addons" / addon)
    shutil.copy2(ROOT / "requirements-extra.txt", staging / "requirements-extra.txt")
    copy_tree(ROOT / "config", staging / "config")
    copy_tree(ROOT / "docs", staging / "docs")
    copy_tree(ROOT / "scripts" / "windows", staging / "scripts" / "windows")

    manifest = {
        "product": PRODUCT,
        "version": version,
        "created_at": created_at,
        "source_commit": source_commit,
        "odoo_commit": ODOO_COMMIT,
        "python_version": PYTHON_VERSION,
        "supported_postgresql": SUPPORTED_POSTGRESQL,
        "included_addons": list(ADDONS),
        "addon_versions": addon_versions,
        "package_sha256": "RECORDED_IN_SIDECAR_MANIFEST",
        "signature": {
            "status": "not_configured",
            "algorithm": None,
            "value": None,
        },
    }
    (staging / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    zip_dir(staging, package_path)
    package_sha = sha256_file(package_path)
    verify_sha = sha256_file(package_path)
    if verify_sha != package_sha:
        fail("SHA256 verification mismatch after package creation.")

    sidecar_manifest = dict(manifest)
    sidecar_manifest["package_sha256"] = package_sha
    sidecar_manifest["package_file"] = package_name
    (out_root / "release-manifest.json").write_text(
        json.dumps(sidecar_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_root / f"{package_name}.sha256").write_text(
        f"{package_sha}  {package_name}\n",
        encoding="ascii",
    )
    shutil.rmtree(staging)
    return package_path


def main(argv: list[str]) -> int:
    package = build(argv[1] if len(argv) > 1 else "")
    print(f"Release package created: {package}")
    print(f"SHA256: {sha256_file(package)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
