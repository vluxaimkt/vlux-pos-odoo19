from __future__ import annotations

import ast
import csv
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADDONS = ("vlux_core", "vlux_mobile_scanner", "vlux_owner", "vlux_pos_catalog", "vlux_facturacion")
SECRET_RE = re.compile(
    r"(?m)^\s*[A-Za-z0-9_.-]*(password|passwd|secret|token|api[_-]?key)"
    r"\s*=\s*(['\"][^'\"]{12,}|[A-Z0-9_./+=-]{12,})"
)
# The addons every productive edition except local_core ships. The same list is
# repeated in files that cannot import each other (Python for Odoo, the cloud
# CLI, the release builder, C#, batch, shell, Dockerfile). This check is the
# single source of truth: adding an addon here fails until every copy agrees.
PRODUCT_ADDONS = ("vlux_core", "vlux_mobile_scanner", "vlux_owner", "vlux_pos_catalog")
PRODUCT_ADDON_COPIES = {
    "packaging/cloud/vlux_cloud.py": r"PRODUCTIVE_ADDONS = \(([^)]*)\)",
    "scripts/release/build_release.py": r"PRODUCT_ADDONS = \(([^)]*)\)",
    "scripts/distribution/vlux_pos.py": r'"local_complete": \(([^)]*)\)',
    "vlux_core/models/system_info.py": r'"local_complete": \(([^)]*)\)',
    "packaging/windows/service-host/Program.cs": r'"-i", "([^"]*)"',
    "scripts/windows/smoke_check.py": r"^mods = \[([^\]]*)\]",
    "scripts/release/cloud_e2e.sh": r'data\["modules_upgraded"\] == \[([^\]]*)\]',
}
PRODUCT_ADDON_MENTIONS = (
    "packaging/cloud/Dockerfile",
    "scripts/windows/02_stage_release.bat",
    "scripts/windows/03_verify_release.bat",
    "scripts/windows/04_upgrade_modules.bat",
    "docs/DISTRIBUCION_PRODUCCION.md",
)
FORBIDDEN_NAMES = {
    ".env",
    "odoo.conf",
    "pgpass.conf",
}
FORBIDDEN_SUFFIXES = {
    ".dump",
    ".backup",
    ".sql",
    ".pem",
    ".key",
    ".pfx",
    ".p12",
    ".log",
    ".zip",
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def addon_files():
    for addon in ADDONS:
        addon_path = ROOT / addon
        if not addon_path.is_dir():
            fail(f"Missing addon: {addon}")
        yield addon_path


def check_manifest(addon_path: Path) -> None:
    manifest_path = addon_path / "__manifest__.py"
    try:
        manifest = ast.literal_eval(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Invalid manifest {manifest_path}: {exc}")
    for key in ("name", "version", "depends", "installable"):
        if key not in manifest:
            fail(f"{manifest_path} missing {key}")


def check_python_syntax(addon_path: Path) -> None:
    for path in addon_path.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            fail(f"Python syntax error in {path}: {exc}")


def check_xml(addon_path: Path) -> None:
    for path in addon_path.rglob("*.xml"):
        try:
            ET.parse(path)
        except ET.ParseError as exc:
            fail(f"XML parse error in {path}: {exc}")


def check_csv(addon_path: Path) -> None:
    for path in addon_path.rglob("*.csv"):
        try:
            with path.open(newline="", encoding="utf-8") as stream:
                for _row in csv.reader(stream):
                    pass
        except Exception as exc:
            fail(f"CSV parse error in {path}: {exc}")


def check_conflicting_files() -> None:
    tracked = subprocess.check_output(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).splitlines()
    for relative in tracked:
        path = ROOT / relative
        parts = {part.lower() for part in Path(relative).parts}
        if parts.intersection({"backups", "filestore", "secrets", "certificates"}):
            fail(f"Forbidden directory in repository: {path}")
        name = path.name.lower()
        if name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            fail(f"Forbidden file in repository: {path}")


def check_obvious_secrets() -> None:
    tracked = subprocess.check_output(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).splitlines()
    for relative in tracked:
        path = ROOT / relative
        if (
            not path.is_file()
            or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pyc"}
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in SECRET_RE.finditer(text):
            if (
                "REEMPLAZAR_" in match.group(0)
                or "SECRETO_" in match.group(0)
                or "__VLUX_" in match.group(0)
                or "example" in match.group(0).lower()
            ):
                continue
            fail(f"Possible hardcoded secret in {path}")


def check_product_addon_lists() -> None:
    expected = set(PRODUCT_ADDONS)
    for rel, pattern in PRODUCT_ADDON_COPIES.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            fail(f"Product addon list not found in {rel}")
        found = set(re.findall(r"vlux_[a-z_]+", match.group(1)))
        if found != expected:
            fail(
                f"Product addon list in {rel} is {sorted(found)}, expected {sorted(expected)}"
            )
    for rel in PRODUCT_ADDON_MENTIONS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        missing = [addon for addon in PRODUCT_ADDONS if addon != "vlux_core" and addon not in text]
        if missing:
            fail(f"{rel} does not mention product addons: {', '.join(missing)}")


def main() -> int:
    for addon_path in addon_files():
        check_manifest(addon_path)
        check_python_syntax(addon_path)
        check_xml(addon_path)
        check_csv(addon_path)
    check_conflicting_files()
    check_obvious_secrets()
    check_product_addon_lists()
    print("Static checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
