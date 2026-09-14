from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[2]
ODOO_COMMIT = "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97"
WIX_NAMESPACE = "http://wixtoolset.org/schemas/v4/wxs"
BAL_NAMESPACE = "http://wixtoolset.org/schemas/v4/wxs/bal"
PRODUCT_UPGRADE_CODE = "11111111-1111-4111-8111-111111111111"
BUNDLE_UPGRADE_CODE = "22222222-2222-4222-8222-222222222222"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(args: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout)
    if completed.returncode:
        raise subprocess.CalledProcessError(completed.returncode, args, completed.stdout)
    return completed.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def msi_version(logical_version: str) -> str:
    parts = re.findall(r"\d+", logical_version)
    if not parts:
        return "0.0.0"
    normalized = (parts + ["0", "0", "0"])[:3]
    return ".".join(str(min(int(part), 65535)) for part in normalized)


def safe_id(value: str, prefix: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"{prefix}_{cleaned}"
    return cleaned[:70]


def deterministic_guid(name: str) -> str:
    return str(uuid.uuid5(uuid.UUID("11111111-2222-4333-8444-555555555555"), name)).upper()


def extract_payload(canonical_zip: Path, payload_dir: Path) -> dict:
    if payload_dir.exists():
        shutil.rmtree(payload_dir)
    payload_dir.mkdir(parents=True)
    with zipfile.ZipFile(canonical_zip) as archive:
        archive.extractall(payload_dir)
    manifest_path = payload_dir / "release-manifest.json"
    if not manifest_path.exists():
        fail(f"Canonical payload is missing release-manifest.json: {canonical_zip}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def generate_product_wxs(payload_dir: Path, product_wxs: Path, version: str) -> None:
    payload_files = [
        path
        for path in sorted(payload_dir.rglob("*"))
        if path.is_file()
        and path.relative_to(payload_dir).as_posix() not in {"release-manifest.json", "checksums.json"}
    ]
    if not payload_files:
        fail("Windows payload is empty; refusing to build MSI.")
    component_refs: list[str] = []

    tree: dict = {"dirs": {}, "files": []}
    for index, path in enumerate(payload_files):
        rel = path.relative_to(payload_dir)
        node = tree
        for part in rel.parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": []})
        component_id = safe_id(f"CMP_{index}_{rel.as_posix()}", "CMP")
        file_id = safe_id(f"FIL_{index}_{rel.as_posix()}", "FIL")
        node["files"].append(
            {
                "component_id": component_id,
                "file_id": file_id,
                "guid": deterministic_guid(rel.as_posix()),
                "source": str(path),
            }
        )
        component_refs.append(f'      <ComponentRef Id="{component_id}" />')

    def emit_directory(node: dict, rel_prefix: str = "", indent: int = 3) -> list[str]:
        lines: list[str] = []
        pad = "  " * indent
        for file_entry in node["files"]:
            lines.extend(
                [
                    f'{pad}<Component Id="{file_entry["component_id"]}" Guid="{file_entry["guid"]}">',
                    f'{pad}  <File Id="{file_entry["file_id"]}" Source="{escape(file_entry["source"])}" KeyPath="yes" />',
                    f"{pad}</Component>",
                ]
            )
        for name, child in sorted(node["dirs"].items()):
            child_rel = f"{rel_prefix}/{name}" if rel_prefix else name
            directory_id = safe_id(f"DIR_{deterministic_guid(child_rel)[:8]}_{name}", "DIR")
            lines.append(f'{pad}<Directory Id="{directory_id}" Name="{escape(name)}">')
            lines.extend(emit_directory(child, child_rel, indent + 1))
            lines.append(f"{pad}</Directory>")
        return lines

    payload_directory = "\n".join(emit_directory(tree, indent=5))
    component_group = "\n".join(component_refs)

    product_wxs.write_text(
        f'''<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="{WIX_NAMESPACE}">
  <Package Name="VLUX POS" Manufacturer="VLUX" Version="{msi_version(version)}" UpgradeCode="{PRODUCT_UPGRADE_CODE}" Scope="perMachine">
    <MajorUpgrade DowngradeErrorMessage="A newer VLUX POS version is already installed." />
    <MediaTemplate EmbedCab="yes" />

    <Feature Id="MainFeature" Title="VLUX POS" Level="1">
      <ComponentGroupRef Id="VLUXProgramFiles" />
      <ComponentRef Id="VLUXProgramDataFolders" />
    </Feature>

    <StandardDirectory Id="ProgramFiles64Folder">
      <Directory Id="VLUXProgramFilesRoot" Name="VLUX">
        <Directory Id="INSTALLFOLDER" Name="POS">
{payload_directory}
        </Directory>
      </Directory>
    </StandardDirectory>

    <StandardDirectory Id="CommonAppDataFolder">
      <Directory Id="VLUXProgramDataRoot" Name="VLUX">
        <Directory Id="VLUXProgramDataPOS" Name="POS">
          <Directory Id="VLUXConfigDir" Name="config" />
          <Directory Id="VLUXDataDir" Name="data" />
          <Directory Id="VLUXFilestoreDir" Name="filestore" />
          <Directory Id="VLUXBackupsDir" Name="backups" />
          <Directory Id="VLUXLogsDir" Name="logs" />
        </Directory>
      </Directory>
    </StandardDirectory>

    <ComponentGroup Id="VLUXProgramFiles">
{component_group}
    </ComponentGroup>

    <Component Id="VLUXProgramDataFolders" Directory="VLUXProgramDataPOS" Guid="33333333-3333-4333-8333-333333333333">
      <CreateFolder Directory="VLUXConfigDir" />
      <CreateFolder Directory="VLUXDataDir" />
      <CreateFolder Directory="VLUXFilestoreDir" />
      <CreateFolder Directory="VLUXBackupsDir" />
      <CreateFolder Directory="VLUXLogsDir" />
    </Component>
  </Package>
</Wix>
''',
        encoding="utf-8",
    )


def generate_bundle_wxs(bundle_wxs: Path, msi_path: Path, version: str) -> None:
    bundle_wxs.write_text(
        f'''<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="{WIX_NAMESPACE}" xmlns:bal="{BAL_NAMESPACE}">
  <Bundle Name="VLUX POS Setup" Manufacturer="VLUX" Version="{msi_version(version)}" UpgradeCode="{BUNDLE_UPGRADE_CODE}">
    <BootstrapperApplication>
      <bal:WixStandardBootstrapperApplication Theme="hyperlinkLicense" LicenseUrl="" />
    </BootstrapperApplication>
    <Chain>
      <MsiPackage SourceFile="{escape(str(msi_path))}" Vital="yes" Compressed="yes" />
    </Chain>
  </Bundle>
</Wix>
''',
        encoding="utf-8",
    )


def build(version: str, canonical_zip: Path, out_dir: Path, wix_version: str) -> dict:
    wix = shutil.which("wix")
    if not wix:
        fail("WiX CLI not found in PATH.")

    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "_wix_work"
    payload_dir = work_dir / "payload"
    work_dir.mkdir(parents=True, exist_ok=True)
    release_manifest = extract_payload(canonical_zip, payload_dir)

    source_commit = release_manifest["source_commit"]
    msi_file = out_dir / f"VLUX_POS_{version}_x64.msi"
    setup_file = out_dir / f"VLUX_POS_Setup_{version}.exe"
    product_wxs = work_dir / "VLUX_POS_Product.generated.wxs"
    bundle_wxs = work_dir / "VLUX_POS_Bundle.generated.wxs"

    generate_product_wxs(payload_dir, product_wxs, version)
    run([wix, "build", str(product_wxs), "-arch", "x64", "-o", str(msi_file)], cwd=ROOT)
    if not msi_file.exists() or msi_file.stat().st_size == 0:
        fail(f"MSI was not created: {msi_file}")

    generate_bundle_wxs(bundle_wxs, msi_file, version)
    run(
        [wix, "build", str(bundle_wxs), "-ext", "WixToolset.Bal.wixext", "-o", str(setup_file)],
        cwd=ROOT,
    )
    if not setup_file.exists() or setup_file.stat().st_size == 0:
        fail(f"Setup EXE was not created: {setup_file}")

    manifest = {
        "target": "windows_local",
        "version": version,
        "source_commit": source_commit,
        "odoo_commit": ODOO_COMMIT,
        "edition": release_manifest["edition"],
        "msi_file": msi_file.name,
        "msi_sha256": sha256_file(msi_file),
        "setup_file": setup_file.name,
        "setup_sha256": sha256_file(setup_file),
        "wix_version": wix_version,
        "signing_status": "PENDING",
        "runtime_chain_status": "PENDING_RUNTIME_CHAIN",
        "validation": {
            "wix_build": "PASS",
            "msi_exists": "PASS",
            "setup_exists": "PASS",
            "install_smoke": "MANUAL_PENDING",
        },
        "build_timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    write_json(out_dir / "manifest.json", manifest)
    (out_dir / f"{msi_file.name}.sha256").write_text(
        f"{manifest['msi_sha256']}  {msi_file.name}\n", encoding="ascii"
    )
    (out_dir / f"{setup_file.name}.sha256").write_text(
        f"{manifest['setup_sha256']}  {setup_file.name}\n", encoding="ascii"
    )
    shutil.rmtree(work_dir)
    return manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build real VLUX POS Windows MSI and Burn EXE with WiX.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--canonical-zip", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--wix-version", required=True)
    args = parser.parse_args(argv[1:])
    manifest = build(args.version, args.canonical_zip.resolve(), args.out_dir.resolve(), args.wix_version)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
