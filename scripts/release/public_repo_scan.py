from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_DIRS = {"filestore", "backups", "secrets", "certificates", "client_data"}
FORBIDDEN_NAMES = {".env", "odoo.conf", "pgpass.conf"}
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
SENSITIVE_RE = re.compile(
    r"(?m)^\s*([A-Za-z0-9_.-]*(password|passwd|secret|token|api[_-]?key)[A-Za-z0-9_.-]*)"
    r"\s*[:=]\s*(['\"][^'\"]{12,}|[A-Z0-9_./{}$-]{12,})"
)
ALLOWLIST_MARKERS = (
    "REEMPLAZAR_",
    "SECRETO_",
    "__VLUX_",
    "<api-key",
    "example",
    "token_urlsafe",
    "PASSWORD_FILE",
)


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def tracked_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    )
    return [line for line in output.splitlines() if line]


def scan_paths(paths: list[str]) -> None:
    for relative in paths:
        path = ROOT / relative
        parts = {part.lower() for part in Path(relative).parts}
        if parts & FORBIDDEN_DIRS:
            fail(f"Forbidden public repository directory: {relative}")
        if path.name.lower() in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            fail(f"Forbidden public repository file: {relative}")


def scan_text(paths: list[str]) -> None:
    for relative in paths:
        path = ROOT / relative
        if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in SENSITIVE_RE.finditer(text):
            line = match.group(0)
            if any(marker.lower() in line.lower() for marker in ALLOWLIST_MARKERS):
                continue
            fail(f"Possible public repo secret in {relative}: {match.group(1)}")


def main() -> int:
    paths = tracked_files()
    scan_paths(paths)
    scan_text(paths)
    print("PUBLIC_REPO_SCAN=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
