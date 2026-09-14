from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import build_release


ROOT = Path(__file__).resolve().parents[2]


def run(args: list[str], cwd: Path = ROOT) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, encoding="utf-8").strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def write_sha256s(directory: Path) -> None:
    lines = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            lines.append(f"{sha256_file(path)}  {path.relative_to(directory).as_posix()}")
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="ascii")


def tool_status(tool: str) -> str:
    return "available" if shutil.which(tool) else "missing"


def build(version: str, out_dir: Path) -> dict:
    if os.environ.get("VLUX_ALLOW_DIRTY_BUILD") != "1":
        build_release.ensure_clean_tree()
    canonical = build_release.build(version, edition="local_complete")
    source_commit = run(["git", "rev-parse", "HEAD"])
    build_date = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "common").mkdir(parents=True)
    shutil.copy2(canonical, out_dir / "common" / canonical.name)
    shutil.copy2(canonical.with_name("release-manifest.json"), out_dir / "common" / "release-manifest.json")

    windows = out_dir / "windows"
    ubuntu = out_dir / "ubuntu"
    cloud = out_dir / "cloud"
    copy_tree(ROOT / "packaging" / "windows", windows / "source")
    copy_tree(ROOT / "packaging" / "ubuntu", ubuntu / "source")
    copy_tree(ROOT / "packaging" / "cloud", cloud / "source")

    common_manifest = json.loads((out_dir / "common" / "release-manifest.json").read_text(encoding="utf-8"))
    target_manifests = {
        "windows": {
            "target": "windows_local",
            "recommended_artifact": f"VLUX_POS_Setup_{version}.exe",
            "internal_msi": f"VLUX_POS_{version}_x64.msi",
            "builder": "WiX Toolset v4 Burn + MSI",
            "wix": tool_status("wix"),
            "signing": "PENDING",
            "general_availability_signing_required": True,
            "status": "source_recipe_ready",
        },
        "ubuntu": {
            "target": "ubuntu_local",
            "recommended_artifact": f"vlux-pos_{version}_amd64.deb",
            "builder": "Debian package scripts",
            "dpkg_deb": tool_status("dpkg-deb"),
            "status": "source_recipe_ready",
        },
        "cloud": {
            "target": "cloud_managed",
            "recommended_artifact": f"vlux-pos:{version}",
            "builder": "OCI image + compose/provision bundle",
            "docker": tool_status("docker"),
            "postgres_in_app_container": False,
            "status": "source_recipe_ready",
        },
    }

    for name, payload in target_manifests.items():
        manifest = dict(common_manifest)
        manifest.update(payload)
        manifest["build_date"] = build_date
        manifest["source_commit"] = source_commit
        write_json(out_dir / name / "manifest.json", manifest)

    summary = {
        "product": "VLUX POS",
        "version": version,
        "source_commit": source_commit,
        "common_release_artifact": str((out_dir / "common" / canonical.name).resolve()),
        "common_release_sha256": sha256_file(out_dir / "common" / canonical.name),
        "targets": target_manifests,
        "manual_validation_required": [
            "WINDOWS_CLEAN_INSTALL",
            "UBUNTU_CLEAN_INSTALL",
            "CLOUD_DOMAIN",
            "PHONE_CAMERA",
            "PHONE_REAL_SCAN",
        ],
    }
    write_json(out_dir / "manifest.json", summary)
    for directory in (out_dir / "common", windows, ubuntu, cloud):
        write_sha256s(directory)
    return summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build VLUX POS distribution metadata and source bundles.")
    parser.add_argument("version")
    parser.add_argument("--out-dir", default=str(ROOT / "dist"))
    args = parser.parse_args(argv[1:])
    summary = build(args.version, Path(args.out_dir))
    print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
