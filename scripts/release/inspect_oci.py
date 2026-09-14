from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from datetime import datetime, timezone
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


def read_json_blob(archive: tarfile.TarFile, digest: str) -> dict:
    algo, value = digest.split(":", 1)
    if algo != "sha256":
        fail(f"Unsupported OCI digest algorithm: {algo}")
    member = archive.extractfile(f"blobs/sha256/{value}")
    if member is None:
        fail(f"Missing OCI blob: {digest}")
    return json.loads(member.read().decode("utf-8"))


def inspect(oci_tar: Path, version: str, source_commit: str, edition: str, out_dir: Path) -> dict:
    if not oci_tar.exists() or oci_tar.stat().st_size == 0:
        fail(f"OCI artifact missing or empty: {oci_tar}")
    with tarfile.open(oci_tar, "r") as archive:
        index_member = archive.extractfile("index.json")
        layout_member = archive.extractfile("oci-layout")
        if index_member is None or layout_member is None:
            fail("Tar is not an OCI layout archive; index.json or oci-layout is missing.")
        index = json.loads(index_member.read().decode("utf-8"))
        manifest_digest = index["manifests"][0]["digest"]
        manifest_blob = read_json_blob(archive, manifest_digest)
        config_blob = read_json_blob(archive, manifest_blob["config"]["digest"])

    labels = config_blob.get("config", {}).get("Labels", {}) or {}
    architecture = config_blob.get("architecture")
    if architecture != "amd64":
        fail(f"Unexpected OCI architecture {architecture!r}; expected amd64.")
    expected_labels = {
        "org.opencontainers.image.title": "VLUX POS",
        "org.opencontainers.image.version": version,
        "org.opencontainers.image.revision": source_commit,
        "vlux.odoo.commit": ODOO_COMMIT,
    }
    missing = {key: value for key, value in expected_labels.items() if labels.get(key) != value}
    if missing:
        fail(f"OCI labels mismatch: {missing}")

    manifest = {
        "target": "cloud_managed",
        "version": version,
        "source_commit": source_commit,
        "odoo_commit": ODOO_COMMIT,
        "edition": edition,
        "artifact_file": oci_tar.name,
        "artifact_sha256": sha256_file(oci_tar),
        "artifact_format": "OCI layout tar",
        "architecture": "amd64",
        "image_labels": labels,
        "validation": {
            "oci_layout": "PASS",
            "image_inspect": "PASS",
            "container_smoke": "PENDING_WORKFLOW",
        },
        "build_timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / f"{oci_tar.name}.sha256").write_text(
        f"{manifest['artifact_sha256']}  {oci_tar.name}\n", encoding="ascii"
    )
    return manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Inspect and manifest a VLUX POS OCI layout tar.")
    parser.add_argument("--oci-tar", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--edition", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv[1:])
    manifest = inspect(
        args.oci_tar.resolve(),
        args.version,
        args.source_commit,
        args.edition,
        args.out_dir.resolve(),
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
