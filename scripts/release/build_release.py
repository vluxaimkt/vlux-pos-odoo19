from __future__ import annotations

import ast
import argparse
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
PRODUCT_ADDONS = ("vlux_core", "vlux_mobile_scanner", "vlux_owner", "vlux_pos_catalog")
REGRESSION_ADDONS = ("vlux_facturacion",)
ADDON_PROFILES = {
    "local_core": ("vlux_core",),
    "local_complete": PRODUCT_ADDONS,
    "cloud_managed": PRODUCT_ADDONS,
}
ALL_KNOWN_ADDONS = PRODUCT_ADDONS + REGRESSION_ADDONS
ODOO_COMMIT = "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97"
PYTHON_VERSION = "3.12.10"
SUPPORTED_POSTGRESQL = "16.14"
PRODUCT = "VLUX POS"
DEFAULT_ODOO_HOME = Path(r"C:\Odoo\src\odoo")

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


def resolve_odoo_home(explicit_path: str | os.PathLike[str] | None = None) -> Path:
    raw_path = explicit_path or os.environ.get("ODOO_HOME") or DEFAULT_ODOO_HOME
    odoo_home = Path(raw_path).expanduser().resolve()
    if not odoo_home.exists():
        fail(f"ODOO_HOME does not exist: {odoo_home}")
    if not (odoo_home / ".git").exists():
        fail(f"ODOO_HOME is not a git checkout: {odoo_home}")
    try:
        inside_work_tree = run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=odoo_home,
        )
    except subprocess.CalledProcessError as exc:
        fail(f"ODOO_HOME is not a valid git checkout: {odoo_home} ({exc})")
    if inside_work_tree != "true":
        fail(f"ODOO_HOME is not a git work tree: {odoo_home}")
    return odoo_home


def validate_odoo_baseline(odoo_home: str | os.PathLike[str] | None = None) -> Path:
    resolved = resolve_odoo_home(odoo_home)
    current = git_sha(resolved)
    if current != ODOO_COMMIT:
        fail(f"Unexpected Odoo commit {current}; expected {ODOO_COMMIT}")
    print(f"Odoo baseline OK: {resolved} ({current})")
    return resolved


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


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_tree(root: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[path.relative_to(root).as_posix()] = sha256_file(path)
    return hashes


def build(
    version: str,
    edition: str = "local_complete",
    odoo_home: str | os.PathLike[str] | None = None,
) -> Path:
    if not version:
        fail("Usage: build_release.py <version> [--edition local_complete]")
    if edition not in ADDON_PROFILES:
        fail(f"Unknown edition {edition!r}. Expected one of: {', '.join(ADDON_PROFILES)}")
    if os.environ.get("VLUX_ALLOW_DIRTY_BUILD") != "1":
        ensure_clean_tree()
    validate_odoo_baseline(odoo_home)

    source_commit = git_sha(ROOT)
    included_addons = ADDON_PROFILES[edition]
    addon_versions = {addon: read_manifest(addon).get("version") for addon in included_addons}
    regression_addon_versions = {
        addon: read_manifest(addon).get("version") for addon in REGRESSION_ADDONS
    }
    build_date = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    out_root = ROOT.parent / "vlux_release_output" / version
    staging = out_root / "staging"
    package_name = f"VLUX_POS_{edition}_{version}.zip"
    package_path = out_root / package_name

    if out_root.exists():
        shutil.rmtree(out_root)
    staging.mkdir(parents=True)

    for addon in included_addons:
        copy_tree(ROOT / addon, staging / "addons" / addon)
    shutil.copy2(ROOT / "requirements-extra.txt", staging / "requirements-extra.txt")
    copy_tree(ROOT / "config", staging / "config")
    copy_tree(ROOT / "docs", staging / "docs")
    copy_tree(ROOT / "scripts" / "distribution", staging / "scripts" / "distribution")
    copy_tree(ROOT / "scripts" / "windows", staging / "scripts" / "windows")
    copy_tree(ROOT / "packaging", staging / "packaging")

    manifest = {
        "product": PRODUCT,
        "version": version,
        "build_date": build_date,
        "source_commit": source_commit,
        "odoo_commit": ODOO_COMMIT,
        "python_version": PYTHON_VERSION,
        "postgresql_version": SUPPORTED_POSTGRESQL,
        "edition": edition,
        "included_addons": list(included_addons),
        "addon_versions": addon_versions,
        "regression_addons_available_in_source": list(REGRESSION_ADDONS),
        "regression_addon_versions": regression_addon_versions,
        "artifact_sha256": "RECORDED_IN_SIDECAR_MANIFEST",
        "logical_release_targets": ["windows_local", "ubuntu_local", "cloud_managed"],
        "facturacion_policy": "simulation_only_not_productive_default",
        "odoo_source": {
            "repository": "https://github.com/odoo/odoo",
            "commit": ODOO_COMMIT,
            "install_strategy": "pinned_checkout_or_internal_mirror",
        },
        "signature": {
            "status": "not_configured",
            "windows_authenticode": "pending",
            "algorithm": None,
            "value": None,
        },
    }
    write_json(staging / "release-manifest.json", manifest)
    write_json(staging / "checksums.json", sha256_tree(staging))

    zip_dir(staging, package_path)
    package_sha = sha256_file(package_path)
    verify_sha = sha256_file(package_path)
    if verify_sha != package_sha:
        fail("SHA256 verification mismatch after package creation.")

    sidecar_manifest = dict(manifest)
    sidecar_manifest["artifact_sha256"] = package_sha
    sidecar_manifest["artifact_file"] = package_name
    write_json(out_root / "release-manifest.json", sidecar_manifest)
    (out_root / f"{package_name}.sha256").write_text(
        f"{package_sha}  {package_name}\n",
        encoding="ascii",
    )
    shutil.rmtree(staging)
    return package_path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build a canonical VLUX POS release artifact.")
    parser.add_argument("version")
    parser.add_argument(
        "--edition",
        choices=sorted(ADDON_PROFILES),
        default="local_complete",
        help="Release edition to include in the canonical artifact.",
    )
    parser.add_argument(
        "--odoo-home",
        help="Path to the pinned Odoo checkout. Defaults to ODOO_HOME, then C:\\Odoo\\src\\odoo.",
    )
    args = parser.parse_args(argv[1:])
    package = build(args.version, edition=args.edition, odoo_home=args.odoo_home)
    print(f"Release package created: {package}")
    print(f"SHA256: {sha256_file(package)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
