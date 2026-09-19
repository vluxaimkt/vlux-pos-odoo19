from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ODOO_COMMIT = "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_target_manifest(root: Path, target: str) -> dict:
    matches = sorted(root.rglob(f"*{target}*/manifest.json"))
    if not matches:
        matches = [path for path in root.rglob("manifest.json") if path.parent.name == target]
    if not matches:
        fail(f"Missing target manifest for {target}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def verify_checksum(root: Path, filename: str, expected: str) -> None:
    matches = sorted(root.rglob(filename))
    if not matches:
        fail(f"Missing artifact referenced by manifest: {filename}")
    actual = sha256_file(matches[0])
    if actual != expected:
        fail(f"Checksum mismatch for {filename}: {actual} != {expected}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Write the global VLUX distribution manifest.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--edition", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--artifacts-root", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv[1:])

    root = args.artifacts_root.resolve()
    out_dir = args.out_dir.resolve()
    windows = read_target_manifest(root, "windows")
    ubuntu = read_target_manifest(root, "ubuntu")
    cloud = read_target_manifest(root, "cloud")

    verify_checksum(root, windows["msi_file"], windows["msi_sha256"])
    verify_checksum(root, windows["setup_file"], windows["setup_sha256"])
    verify_checksum(root, ubuntu["deb_file"], ubuntu["deb_sha256"])
    verify_checksum(root, cloud["artifact_file"], cloud["artifact_sha256"])

    manifest = {
        "product": "VLUX POS",
        "version": args.version,
        "source_commit": args.source_commit,
        "odoo_commit": ODOO_COMMIT,
        "edition": args.edition,
        "repository_visibility": "public",
        "production_go": "NOT_YET",
        "targets": {
            "windows": windows,
            "ubuntu": ubuntu,
            "cloud": cloud,
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "distribution-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = []
    for artifact in sorted(root.rglob("*")):
        if artifact.is_file() and artifact.suffix.lower() in {".msi", ".exe", ".deb", ".tar", ".json"}:
            lines.append(f"{sha256_file(artifact)}  {artifact.name}")
    (out_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
