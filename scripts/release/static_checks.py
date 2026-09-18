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


def main() -> int:
    for addon_path in addon_files():
        check_manifest(addon_path)
        check_python_syntax(addon_path)
        check_xml(addon_path)
        check_csv(addon_path)
    check_conflicting_files()
    check_obvious_secrets()
    print("Static checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
