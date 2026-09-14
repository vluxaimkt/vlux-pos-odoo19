from __future__ import annotations

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
ODOO_COMMIT = "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, encoding="utf-8").strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_executable(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    dst.chmod(0o755)


def extract_payload(canonical_zip: Path, target: Path) -> dict:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(canonical_zip) as archive:
        archive.extractall(target)
    manifest_path = target / "release-manifest.json"
    if not manifest_path.exists():
        fail(f"Canonical payload is missing release-manifest.json: {canonical_zip}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def write_control(version: str, target: Path) -> None:
    source = ROOT / "packaging" / "ubuntu" / "debian" / "control"
    content = source.read_text(encoding="utf-8").splitlines()
    replaced = []
    for line in content:
        if line.startswith("Version: "):
            replaced.append(f"Version: {version}")
        else:
            replaced.append(line)
    target.write_text("\n".join(replaced) + "\n", encoding="utf-8")


def build(version: str, canonical_zip: Path, out_dir: Path) -> dict:
    if not shutil.which("dpkg-deb"):
        fail("dpkg-deb is required to build the Ubuntu package.")

    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "_deb_work"
    package_root = work_dir / "root"
    payload_dir = package_root / "opt" / "vlux" / "pos"
    debian_dir = package_root / "DEBIAN"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    debian_dir.mkdir(parents=True)

    release_manifest = extract_payload(canonical_zip, payload_dir)
    source_commit = release_manifest["source_commit"]

    write_control(version, debian_dir / "control")
    for script_name in ("preinst", "postinst", "prerm", "postrm"):
        copy_executable(ROOT / "packaging" / "ubuntu" / "debian" / script_name, debian_dir / script_name)

    copy_executable(ROOT / "scripts" / "distribution" / "vlux-pos", package_root / "usr" / "bin" / "vlux-pos")
    copy_executable(ROOT / "scripts" / "distribution" / "vlux_pos.py", package_root / "opt" / "vlux" / "pos" / "scripts" / "distribution" / "vlux_pos.py")
    (package_root / "lib" / "systemd" / "system").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "packaging" / "ubuntu" / "systemd" / "vlux-pos.service",
        package_root / "lib" / "systemd" / "system" / "vlux-pos.service",
    )
    (package_root / "etc" / "vlux-pos").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "packaging" / "ubuntu" / "caddy" / "Caddyfile.example",
        package_root / "etc" / "vlux-pos" / "Caddyfile.example",
    )
    for rel in (
        "var/lib/vlux-pos/filestore",
        "var/backups/vlux-pos",
        "var/log/vlux-pos",
    ):
        (package_root / rel).mkdir(parents=True, exist_ok=True)

    deb_file = out_dir / f"vlux-pos_{version}_amd64.deb"
    run(["dpkg-deb", "--build", "--root-owner-group", str(package_root), str(deb_file)])
    if not deb_file.exists() or deb_file.stat().st_size == 0:
        fail(f"DEB was not created: {deb_file}")

    info = run(["dpkg-deb", "--info", str(deb_file)])
    contents = run(["dpkg-deb", "--contents", str(deb_file)])
    required_paths = (
        "./opt/vlux/pos/",
        "./usr/bin/vlux-pos",
        "./lib/systemd/system/vlux-pos.service",
        "./etc/vlux-pos/Caddyfile.example",
        "./var/lib/vlux-pos/filestore/",
        "./var/backups/vlux-pos/",
        "./var/log/vlux-pos/",
    )
    missing = [path for path in required_paths if path not in contents]
    if missing:
        fail(f"DEB content validation failed; missing: {', '.join(missing)}")

    manifest = {
        "target": "ubuntu_local",
        "version": version,
        "source_commit": source_commit,
        "odoo_commit": ODOO_COMMIT,
        "edition": release_manifest["edition"],
        "deb_file": deb_file.name,
        "deb_sha256": sha256_file(deb_file),
        "architecture": "amd64",
        "validation": {
            "dpkg_build": "PASS",
            "dpkg_info": "PASS" if "Package: vlux-pos" in info else "FAIL",
            "dpkg_contents": "PASS",
            "install_smoke": "PENDING_WORKFLOW",
        },
        "build_timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if manifest["validation"]["dpkg_info"] != "PASS":
        fail("dpkg-deb --info did not report Package: vlux-pos")
    write_json(out_dir / "manifest.json", manifest)
    (out_dir / f"{deb_file.name}.sha256").write_text(
        f"{manifest['deb_sha256']}  {deb_file.name}\n", encoding="ascii"
    )
    shutil.rmtree(work_dir)
    return manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build a real VLUX POS Ubuntu .deb package.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--canonical-zip", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv[1:])
    manifest = build(args.version, args.canonical_zip.resolve(), args.out_dir.resolve())
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
