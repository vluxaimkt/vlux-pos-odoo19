from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from build_release import ODOO_COMMIT, PYTHON_VERSION, fail, resolve_odoo_home, validate_odoo_baseline


ROOT = Path(__file__).resolve().parents[2]
POSTGRESQL_VERSION = "16.14"
CADDY_VERSION = "2.10.2"
PYTHON_EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
POSTGRESQL_BINARIES_SHA256 = "98af1417ba6a8dc30543e560e5407833a3b9e7cc7ed20e73b2006f3aa2f04663"
CADDY_SHA256 = "9fd1ef9be5d9b05852b66ccc25f96f23d8651bcab20779861a745bdffa273722"

RUNTIME_DOWNLOADS = {
    "python": {
        "file": f"python-{PYTHON_VERSION}-embed-amd64.zip",
        "url": f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip",
        "sha256": PYTHON_EMBED_SHA256,
    },
    "postgresql": {
        "file": f"postgresql-{POSTGRESQL_VERSION}-1-windows-x64-binaries.zip",
        "url": f"https://get.enterprisedb.com/postgresql/postgresql-{POSTGRESQL_VERSION}-1-windows-x64-binaries.zip",
        "sha256": POSTGRESQL_BINARIES_SHA256,
    },
    "caddy": {
        "file": f"caddy_{CADDY_VERSION}_windows_amd64.zip",
        "url": f"https://github.com/caddyserver/caddy/releases/download/v{CADDY_VERSION}/caddy_{CADDY_VERSION}_windows_amd64.zip",
        "sha256": CADDY_SHA256,
    },
}

EXCLUDED_ODDO_DIRS = {
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "debian",
    "demo",
    "doc",
    "docs",
    "test",
    "tests",
}
EXCLUDED_ODDO_SUFFIXES = {
    ".backup",
    ".bak",
    ".crt",
    ".dump",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".pyo",
    ".sql",
}


def run(args: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
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


def download_verified(name: str, cache_dir: Path) -> Path:
    item = RUNTIME_DOWNLOADS[name]
    target = cache_dir / item["file"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        print(f"Downloading {item['url']}")
        with urllib.request.urlopen(item["url"]) as response, target.open("wb") as stream:
            shutil.copyfileobj(response, stream)
    actual = sha256_file(target)
    if actual.lower() != item["sha256"].lower():
        fail(f"{name} checksum mismatch for {target}: {actual} != {item['sha256']}")
    return target


def extract_zip(zip_path: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(destination)


def copy_tree(src: Path, dst: Path, *, exclude_odoo_noise: bool = False) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if exclude_odoo_noise:
            lower_parts = {part.lower() for part in rel.parts}
            if lower_parts.intersection(EXCLUDED_ODDO_DIRS) or path.suffix.lower() in EXCLUDED_ODDO_SUFFIXES:
                continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def normalize_python_embed(python_dir: Path) -> None:
    pth_files = list(python_dir.glob("python*._pth"))
    if not pth_files:
        fail(f"Cannot find Python embeddable ._pth file in {python_dir}")
    pth = pth_files[0]
    lines = []
    for line in pth.read_text(encoding="utf-8").splitlines():
        if line.strip() == "#import site":
            continue
        lines.append(line)
    for required in (".", "Lib\\site-packages", "import site"):
        if required not in lines:
            lines.append(required)
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (python_dir / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)


def prepare_python_runtime(downloads: dict[str, Path], out_dir: Path, odoo_home: Path) -> dict:
    python_dir = out_dir / "runtime" / "python"
    extract_zip(downloads["python"], python_dir)
    normalize_python_embed(python_dir)

    wheelhouse = out_dir / "runtime" / "wheelhouse"
    if wheelhouse.exists():
        shutil.rmtree(wheelhouse)
    wheelhouse.mkdir(parents=True)
    requirements = [odoo_home / "requirements.txt", ROOT / "requirements-extra.txt"]
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--wheel-dir",
            str(wheelhouse),
            "--prefer-binary",
            *sum((["-r", str(path)] for path in requirements), []),
            "pip==24.3.1",
            "setuptools==75.8.0",
            "wheel==0.45.1",
        ]
    )
    site_packages = python_dir / "Lib" / "site-packages"
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--target",
            str(site_packages),
            *sum((["-r", str(path)] for path in requirements), []),
            "pip==24.3.1",
            "setuptools==75.8.0",
            "wheel==0.45.1",
        ]
    )

    python_exe = python_dir / "python.exe"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(out_dir / "runtime" / "odoo")
    run([str(python_exe), "-m", "pip", "check"], env=env)
    run(
        [
            str(python_exe),
            "-c",
            "import sys; import psycopg2; import qrcode; import PIL; print('import smoke ok')",
        ],
        env=env,
    )

    wheels = {
        path.name: sha256_file(path)
        for path in sorted(wheelhouse.glob("*"))
        if path.is_file()
    }
    write_json(out_dir / "runtime" / "wheelhouse-manifest.json", wheels)
    return {
        "source": RUNTIME_DOWNLOADS["python"]["url"],
        "version": PYTHON_VERSION,
        "sha256": PYTHON_EMBED_SHA256,
        "embedded": True,
        "wheelhouse_files": len(wheels),
        "pip_check": "PASS",
        "dependency_mode": "offline_wheelhouse_no_index",
    }


def prepare_postgresql_runtime(downloads: dict[str, Path], out_dir: Path) -> dict:
    temp = out_dir / "_postgresql_extract"
    extract_zip(downloads["postgresql"], temp)
    candidates = [path for path in temp.rglob("postgres.exe") if path.parent.name.lower() == "bin"]
    if not candidates:
        fail("Cannot find postgres.exe in PostgreSQL binaries archive.")
    pg_root = candidates[0].parent.parent
    runtime_pg = out_dir / "runtime" / "postgresql"
    if runtime_pg.exists():
        shutil.rmtree(runtime_pg)
    runtime_pg.mkdir(parents=True)
    for name in ("bin", "lib", "share"):
        copy_tree(pg_root / name, runtime_pg / name)
    for name in ("server_license.txt", "commandlinetools_3rd_party_licenses.txt"):
        source = pg_root / name
        if source.exists():
            shutil.copy2(source, runtime_pg / name)
    shutil.rmtree(temp)
    return {
        "source": RUNTIME_DOWNLOADS["postgresql"]["url"],
        "version": POSTGRESQL_VERSION,
        "sha256": POSTGRESQL_BINARIES_SHA256,
        "embedded": True,
        "local_only_default": True,
    }


def prepare_caddy_runtime(downloads: dict[str, Path], out_dir: Path) -> dict:
    temp = out_dir / "_caddy_extract"
    extract_zip(downloads["caddy"], temp)
    caddy_candidates = list(temp.rglob("caddy.exe"))
    if not caddy_candidates:
        fail("Cannot find caddy.exe in Caddy archive.")
    caddy_dir = out_dir / "runtime" / "caddy"
    if caddy_dir.exists():
        shutil.rmtree(caddy_dir)
    caddy_dir.mkdir(parents=True)
    shutil.copy2(caddy_candidates[0], caddy_dir / "caddy.exe")
    shutil.rmtree(temp)
    return {
        "source": RUNTIME_DOWNLOADS["caddy"]["url"],
        "version": CADDY_VERSION,
        "sha256": CADDY_SHA256,
        "embedded": True,
        "https_runtime": "embedded_configurable",
    }


def prepare_odoo_runtime(out_dir: Path, odoo_home: Path) -> dict:
    odoo_runtime = out_dir / "runtime" / "odoo"
    copy_tree(odoo_home, odoo_runtime, exclude_odoo_noise=True)
    (odoo_runtime / "ODOO_COMMIT.txt").write_text(ODOO_COMMIT + "\n", encoding="ascii")
    return {"commit": ODOO_COMMIT, "embedded": True}


def build(out_dir: Path, odoo_home: Path, cache_dir: Path, version: str) -> dict:
    validate_odoo_baseline(odoo_home)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    downloads = {name: download_verified(name, cache_dir) for name in RUNTIME_DOWNLOADS}

    odoo = prepare_odoo_runtime(out_dir, odoo_home)
    python = prepare_python_runtime(downloads, out_dir, odoo_home)
    postgresql = prepare_postgresql_runtime(downloads, out_dir)
    caddy = prepare_caddy_runtime(downloads, out_dir)

    manifest = {
        "product": "VLUX POS",
        "mode": "windows_turnkey_lab",
        "version": version,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "runtime_chain_status": "COMPLETE",
        "installer_mode": "OFFLINE",
        "python_runtime": python,
        "postgresql_runtime": postgresql,
        "odoo_runtime": odoo,
        "https": caddy,
        "service_host": {
            "name": "VLUXPOS",
            "status": "embedded",
            "starts": ["postgresql", "odoo", "caddy"],
        },
    }
    write_json(out_dir / "windows-turnkey" / "runtime-manifest.json", manifest)
    return manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build the offline Windows runtime payload for VLUX POS.")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--odoo-home", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=ROOT.parent / ".vlux_runtime_cache")
    parser.add_argument("--version", default="0.0.0-turnkey2")
    args = parser.parse_args(argv[1:])
    odoo_home = validate_odoo_baseline(resolve_odoo_home(args.odoo_home))
    manifest = build(args.out_dir.resolve(), odoo_home, args.cache_dir.resolve(), args.version)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
