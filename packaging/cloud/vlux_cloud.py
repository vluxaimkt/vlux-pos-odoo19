#!/usr/bin/env python3
"""vlux-cloud - operational CLI for VLUX POS Cloud Managed hosts.

VLUX operates this tool on Ubuntu 24.04 LTS hosts. It is not a customer
installer. One host runs exactly one VLUX EDGE (Caddy) stack that owns ports
80/443, plus one isolated stack per tenant: its own Odoo container, its own
PostgreSQL container, its own database, role, filestore, secrets, logs, backups
and private network.

Layout on the host::

    /srv/vlux-pos/
        edge/
            Caddyfile compose.yaml .env
            tenants/<tenant>.caddy
            data/ config/ logs/
        tenants/<tenant>/
            compose.yaml .env tenant.json
            config/ filestore/ postgres/ secrets/ logs/ backups/
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "templates"
EDGE_TEMPLATE_DIR = ROOT / "edge"

DEFAULT_BASE_DIR = Path(os.environ.get("VLUX_CLOUD_BASE", "/srv/vlux-pos"))

CLOUD_VERSION = "0.0.0-cloud1"
ODOO_COMMIT = "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97"
PYTHON_VERSION = "3.12.10"

CLOUD_DB_ENGINE = "PostgreSQL"
CLOUD_DB_MAJOR = "16"
CLOUD_DB_TARGET = "16.15"
POSTGRES_IMAGE = (
    "postgres:16.15@sha256:"
    "f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94"
)
POSTGRES_AMD64_DIGEST = (
    "sha256:485935f94cc7165afa896978809c37b592dc07f0a37d2c8f645f12412d0212c8"
)
CADDY_IMAGE = (
    "caddy:2.10@sha256:"
    "c3d7ee5d2b11f9dc54f947f68a734c84e9c9666c92c88a7f30b9cba5da182adb"
)
DEFAULT_APP_IMAGE = "ghcr.io/vluxaimkt/vlux-pos:" + CLOUD_VERSION

EDGE_NETWORK = "vlux-edge"
CONTAINER_PREFIX = "vlux-"

# Edge operating modes. "public_acme" terminates HTTPS on the host with real
# ACME certificates. "cloudflare_tunnel" leaves TLS termination to Cloudflare
# and serves plain HTTP inside the Docker network only.
EDGE_MODES = ("public_acme", "cloudflare_tunnel")
DEFAULT_EDGE_MODE = "public_acme"

# cloudflared is distroless: it runs as uid 65532 and ships no shell, so the
# token file must be readable by that account and health is probed over its
# metrics endpoint rather than with a shell healthcheck.
CLOUDFLARED_IMAGE = (
    "cloudflare/cloudflared:2026.8.3@sha256:"
    "51c9cefcb4569df44e1ad403ab1d3d8065aa8e84339bcfc6aee75502e1140339"
)
CLOUDFLARED_AMD64_DIGEST = (
    "sha256:9be48e4b4e996da851bf78f7782bfab150dd4d8889e469d004802e7d7afb63b1"
)
CLOUDFLARED_UID = 65532
CLOUDFLARED_GID = 65532
CLOUDFLARED_METRICS_PORT = 2000

DATA_CLASSES = ("DEMO", "REAL_CLIENT_DATA")
DEFAULT_DATA_CLASS = "DEMO"
DEPLOYMENT_CLASSES = ("CLOUDFLARE_TEMPORARY_STAGING", "CLOUD_MANAGED_VPS")

MIGRATION_IMPORT_CONFIRM = "IMPORT_TENANT"
CUTOVER_CONFIRM = "CUTOVER"

# Caddy site option emitted for lab TLS. Kept as a constant so the tab that
# Caddyfile blocks require survives every template round trip.
TLS_INTERNAL_LINE = "\ttls internal\n"
HOST_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,15}$")
APP_UID = 10001
APP_GID = 10001
# The postgres entrypoint re-execs itself as the postgres account via gosu, so
# the bind-mounted PGDATA parent must be owned by that account rather than root.
POSTGRES_UID = 999
POSTGRES_GID = 999

HTTP_PORT = 8069
GEVENT_PORT = 8072
DB_PORT = 5432

DEFAULT_WORKERS = 2
DEFAULT_MAX_CRON_THREADS = 1

PRODUCTIVE_ADDONS = ("vlux_core", "vlux_mobile_scanner", "vlux_owner")
OWNER_GROUP_XMLID = "vlux_core.group_vlux_owner"

TENANT_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[a-z0-9.-]{1,189}\.[a-z]{2,}$", re.IGNORECASE)
RESERVED_TENANTS = {"edge", "tenants", "vlux", "app", "postgres", "caddy", "host"}

TLS_MODES = ("public", "internal")
RESTORE_CONFIRM = "RESTORE_TENANT"

# SigV4 canonical hash of an empty request body (GET/HEAD/zero-length PUT).
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


# --------------------------------------------------------------------------
# process and filesystem helpers
# --------------------------------------------------------------------------


class VluxError(SystemExit):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(code)
        self.message = message


def fail(message: str, code: int = 1) -> None:
    raise VluxError(message, code)


def info(message: str) -> None:
    print("[vlux-cloud] " + message, flush=True)


def run(
    args: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    binary: bool = False,
    input_text: str | None = None,
    input_bytes: bytes | None = None,
    stdout_path: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess. Stdin is never echoed because it can carry secrets."""
    kwargs: dict = {"check": False}
    if env is not None:
        kwargs["env"] = env
    if timeout is not None:
        kwargs["timeout"] = timeout
    if input_bytes is not None:
        kwargs["input"] = input_bytes
    elif input_text is not None:
        kwargs["input"] = input_text.encode("utf-8")
    else:
        kwargs["stdin"] = subprocess.DEVNULL
    stdout_handle = None
    if stdout_path is not None:
        stdout_handle = stdout_path.open("wb")
    elif capture or binary:
        stdout_handle = subprocess.PIPE
    try:
        result = subprocess.run(
            args, stdout=stdout_handle, stderr=subprocess.PIPE, **kwargs
        )
    except FileNotFoundError:
        fail("Required executable not found: " + args[0])
        raise
    except subprocess.TimeoutExpired:
        fail("Command timed out: " + " ".join(args[:3]))
        raise
    finally:
        if stdout_path is not None and stdout_handle is not None:
            stdout_handle.close()
    if check and result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", "replace").strip()
        fail(
            "Command failed ("
            + str(result.returncode)
            + "): "
            + " ".join(args[:4])
            + "\n"
            + stderr
        )
    if capture and not binary and result.stdout is not None:
        result.stdout = result.stdout.decode("utf-8", "replace")
    return result


def require_root() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        fail("vlux-cloud must run as root on the cloud host (use sudo).")


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def utc_iso() -> str:
    return utc_now().isoformat()


def utc_stamp() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def ensure_dir(path: Path, mode: int, uid: int | None = None, gid: int | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    if uid is not None and gid is not None:
        os.chown(path, uid, gid)


def write_file(
    path: Path,
    content: str,
    mode: int,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Write atomically with the restrictive mode applied before content lands."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(tmp, mode)
    if uid is not None and gid is not None:
        os.chown(tmp, uid, gid)
    os.replace(tmp, path)


def write_json(
    path: Path,
    payload: dict,
    mode: int = 0o640,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    write_file(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", mode, uid, gid)


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail("Malformed JSON in " + str(path) + ": " + str(exc))
    return {}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render(template: Path, mapping: dict[str, str]) -> str:
    if not template.exists():
        fail("Missing template: " + str(template))
    text = template.read_text(encoding="utf-8")
    for key, value in mapping.items():
        text = text.replace(key, value)
    leftovers = sorted(set(re.findall(r"__[A-Z0-9_]+__", text)))
    if leftovers:
        fail(
            "Unresolved template placeholders in "
            + template.name
            + ": "
            + ", ".join(leftovers)
        )
    return text


def du_bytes(path: Path) -> int:
    total = 0
    for current, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError:
                continue
    return total


def chown_tree(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid)
    for current, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chown(os.path.join(current, name), uid, gid)
            except OSError:
                continue


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def validate_tenant(tenant: str) -> str:
    if not TENANT_RE.match(tenant):
        fail(
            "Invalid tenant slug "
            + repr(tenant)
            + ": use 3-32 lowercase letters, digits and dashes, starting with a letter."
        )
    if tenant in RESERVED_TENANTS:
        fail("Reserved tenant slug: " + tenant)
    return tenant


def validate_domain(domain: str) -> str:
    domain = domain.strip().lower().rstrip(".")
    if not DOMAIN_RE.match(domain):
        fail("Invalid domain " + repr(domain) + ": expected a fully qualified hostname.")
    if domain.startswith("*"):
        fail("Wildcard domains are not supported by cloud provisioning.")
    return domain


def validate_email(email: str) -> str:
    if not EMAIL_RE.match(email.strip()):
        fail("Invalid owner email: " + repr(email))
    return email.strip()


def db_identifier(tenant: str) -> str:
    name = "vlux_" + tenant.replace("-", "_")
    if len(name) > 63:
        fail("Tenant slug too long: derived identifier " + name + " exceeds 63 characters.")
    return name


def dns_preflight(domain: str, tls_mode: str, skip: bool) -> dict:
    """Resolve the tenant domain before claiming a public HTTPS deployment."""
    result = {"domain": domain, "checked": False, "resolved": False, "addresses": []}
    if tls_mode != "public":
        result["status"] = "SKIPPED_LAB_TLS_INTERNAL"
        return result
    if skip:
        result["status"] = "SKIPPED_BY_OPERATOR"
        return result
    result["checked"] = True
    try:
        entries = socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        result["status"] = "UNRESOLVED"
        result["error"] = str(exc)
        fail(
            "DNS preflight failed for "
            + domain
            + " ("
            + str(exc)
            + "). Publish an A/AAAA record pointing at this host, or re-run with "
            "--skip-dns-check to provision without claiming public HTTPS."
        )
    addresses = sorted({entry[4][0] for entry in entries})
    result["resolved"] = True
    result["addresses"] = addresses
    result["status"] = "RESOLVED"
    return result


# --------------------------------------------------------------------------
# host layout
# --------------------------------------------------------------------------


class Layout:
    """Filesystem and Docker naming for one VLUX host.

    ``host_id`` namespaces the Docker objects so a second, independent host can
    be simulated on the same machine (used by the migration drill). The default
    empty namespace keeps the production names unchanged.
    """

    def __init__(self, base: Path, host_id: str = "") -> None:
        self.base = base
        self.host_id = host_id
        self.edge = base / "edge"
        self.edge_tenants = self.edge / "tenants"
        self.edge_compose = self.edge / "compose.yaml"
        self.edge_caddyfile = self.edge / "Caddyfile"
        self.edge_env = self.edge / ".env"
        self.edge_data = self.edge / "data"
        self.edge_config = self.edge / "config"
        self.edge_logs = self.edge / "logs"
        self.tenants = base / "tenants"
        self.host_state = base / "host.json"
        self.offsite_config = base / "offsite.json"
        self.secrets = base / "secrets"
        self.tunnel = base / "tunnel"

    @property
    def container_prefix(self) -> str:
        return CONTAINER_PREFIX if not self.host_id else CONTAINER_PREFIX + self.host_id + "-"

    @property
    def edge_network(self) -> str:
        return EDGE_NETWORK if not self.host_id else EDGE_NETWORK + "-" + self.host_id

    @property
    def edge_container(self) -> str:
        return self.container_prefix + "edge-caddy"

    @property
    def tunnel_container(self) -> str:
        return self.container_prefix + "cloudflared"

    @property
    def tunnel_token_file(self) -> Path:
        return self.secrets / "cloudflare_tunnel_token"

    def edge_mode(self) -> str:
        return read_json(self.host_state).get("edge_mode", DEFAULT_EDGE_MODE)

    def tenant(self, tenant: str) -> "TenantPaths":
        return TenantPaths(self.tenants / validate_tenant(tenant), tenant, self)

    @staticmethod
    def load(base: Path, host_id: str | None = None) -> "Layout":
        """Build a Layout, taking the namespace from host.json unless overridden."""
        stored = read_json(base / "host.json").get("host_id", "")
        resolved = host_id if host_id is not None else stored
        if resolved and not HOST_ID_RE.match(resolved):
            fail("Invalid --host-id " + repr(resolved))
        return Layout(base, resolved or "")

    def local_ca(self) -> Path:
        return self.edge_data / "caddy" / "pki" / "authorities" / "local" / "root.crt"

    def known_tenants(self) -> list[str]:
        if not self.tenants.is_dir():
            return []
        return sorted(
            path.name
            for path in self.tenants.iterdir()
            if path.is_dir() and (path / "tenant.json").exists()
        )


class TenantPaths:
    def __init__(self, root: Path, tenant: str, layout: Layout) -> None:
        self.root = root
        self.name = tenant
        self.layout = layout
        self.config = root / "config"
        self.odoo_conf = self.config / "odoo.conf"
        self.filestore = root / "filestore"
        self.postgres = root / "postgres"
        self.secrets = root / "secrets"
        self.logs = root / "logs"
        self.backups = root / "backups"
        self.compose = root / "compose.yaml"
        self.env_file = root / ".env"
        self.metadata = root / "tenant.json"

    @property
    def caddy_route(self) -> Path:
        return self.layout.edge_tenants / (self.name + ".caddy")

    @property
    def project(self) -> str:
        return self.layout.container_prefix + self.name

    @property
    def app_container(self) -> str:
        return self.project + "-app"

    @property
    def pg_container(self) -> str:
        return self.project + "-postgres"

    @property
    def private_network(self) -> str:
        return self.project + "-private"

    @property
    def app_alias(self) -> str:
        return self.name + "-app"

    def exists(self) -> bool:
        return self.metadata.exists()

    def require(self) -> dict:
        if not self.exists():
            fail("Unknown tenant: " + self.name)
        return read_json(self.metadata)

    def secret(self, name: str) -> str:
        path = self.secrets / name
        if not path.exists():
            fail("Missing tenant secret: " + name)
        return path.read_text(encoding="utf-8").strip("\n")

    def has_secret(self, name: str) -> bool:
        return (self.secrets / name).exists()

    def write_secret(self, name: str, value: str) -> None:
        write_file(self.secrets / name, value, 0o600)


# --------------------------------------------------------------------------
# docker helpers
# --------------------------------------------------------------------------


def docker(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return run(["docker", *args], **kwargs)


def compose(project_dir: Path, args: list[str], **kwargs) -> subprocess.CompletedProcess:
    base = [
        "docker",
        "compose",
        "--project-directory",
        str(project_dir),
        "-f",
        str(project_dir / "compose.yaml"),
    ]
    env_file = project_dir / ".env"
    if env_file.exists():
        base += ["--env-file", str(env_file)]
    return run(base + args, **kwargs)


def container_state(name: str) -> dict:
    result = docker(
        ["inspect", "--format", "{{json .State}}", name], check=False, capture=True
    )
    if result.returncode != 0:
        return {"Status": "absent", "Health": None}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"Status": "unknown", "Health": None}


def container_health(name: str) -> str:
    state = container_state(name)
    health = state.get("Health") or {}
    if health:
        return str(health.get("Status") or "unknown")
    status = str(state.get("Status") or "absent")
    return "running-no-healthcheck" if status == "running" else status


def image_digest(reference: str) -> str:
    result = docker(
        ["image", "inspect", "--format", "{{json .RepoDigests}}", reference],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        return "UNKNOWN"
    try:
        digests = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "UNKNOWN"
    for item in digests:
        if "@" in item:
            return item.split("@", 1)[1]
    if "@sha256:" in reference:
        return reference.split("@", 1)[1]
    return "UNKNOWN"


def postgres_account(image: str) -> tuple[int, int]:
    """Resolve the uid/gid the postgres server will actually run as."""
    result = docker(
        ["run", "--rm", "--entrypoint", "sh", image, "-c", "id -u postgres; id -g postgres"],
        check=False,
        capture=True,
    )
    if result.returncode == 0:
        values = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if len(values) >= 2 and values[0].isdigit() and values[1].isdigit():
            return int(values[0]), int(values[1])
    info("Could not resolve the postgres account from the image; using the pinned default.")
    return POSTGRES_UID, POSTGRES_GID


def network_exists(name: str) -> bool:
    result = docker(
        ["network", "inspect", name, "--format", "{{.Name}}"], check=False, capture=True
    )
    return result.returncode == 0


def wait_for(
    predicate,
    *,
    timeout: int,
    interval: float = 3.0,
    description: str = "condition",
) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        ok, detail = predicate()
        if ok:
            return
        last = detail
        time.sleep(interval)
    fail("Timed out waiting for " + description + " after " + str(timeout) + "s. " + last)


def container_runtime_state(name: str) -> tuple[str, str, int, int]:
    """Return (status, health, restart_count, exit_code) for one container."""
    fmt = (
        "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
        "|{{.RestartCount}}|{{.State.ExitCode}}"
    )
    result = docker(["inspect", "--format", fmt, name], check=False, capture=True)
    if result.returncode != 0:
        return "absent", "none", 0, 0
    parts = (result.stdout or "").strip().split("|")
    if len(parts) != 4:
        return "unknown", "none", 0, 0
    restarts = int(parts[2]) if parts[2].isdigit() else 0
    exit_code = int(parts[3]) if parts[3].lstrip("-").isdigit() else 0
    return parts[0], parts[1], restarts, exit_code


def wait_container_healthy(container: str, timeout: int, *, max_restarts: int = 3) -> None:
    """Wait for health, but give up early when the container is crash-looping.

    Without this a misconfigured container burns the whole timeout reporting
    "unhealthy" while it restarts in the background.
    """
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        status, health, restarts, exit_code = container_runtime_state(container)
        if health == "healthy" or (health == "none" and status == "running"):
            return
        if restarts >= max_restarts:
            fail(
                "Container " + container + " is crash-looping: " + str(restarts)
                + " restarts, last exit code " + str(exit_code)
                + ". Inspect it with: docker logs " + container
            )
        if status == "exited" and exit_code != 0:
            fail(
                "Container " + container + " exited with code " + str(exit_code)
                + ". Inspect it with: docker logs " + container
            )
        last = (
            "status=" + status + " health=" + health
            + " restarts=" + str(restarts) + " exit=" + str(exit_code)
        )
        time.sleep(3)
    fail(
        "Timed out waiting for container " + container + " to become healthy after "
        + str(timeout) + "s. " + last
    )


# --------------------------------------------------------------------------
# HTTP probes
# --------------------------------------------------------------------------


def build_ssl_context(layout: Layout, tls_mode: str) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if tls_mode == "internal":
        ca = layout.local_ca()
        if ca.exists():
            context.load_verify_locations(cafile=str(ca))
        else:
            # Lab TLS before the edge has minted its local CA: verification is
            # not meaningful yet, and this mode is never used in production.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
    return context


def http_probe(url: str, *, context: ssl.SSLContext | None = None, timeout: int = 10) -> tuple[int, str]:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "vlux-cloud"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            body = response.read(65536).decode("utf-8", "replace")
            return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(4096).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - probe result is reported, not raised
        return 0, str(exc)


def probe_image(layout: "Layout") -> str:
    """An image that is present on this host and carries curl.

    Recorded at provision time so probes never depend on pulling a registry
    image that a disconnected or lab host may not have.
    """
    recorded = read_json(layout.host_state).get("probe_image")
    if recorded:
        return recorded
    for name in layout.known_tenants():
        reference = read_json(layout.tenant(name).metadata).get("image", {}).get("reference")
        if reference:
            return reference
    return DEFAULT_APP_IMAGE


def edge_probe(
    layout: "Layout", domain: str, db_name: str, timeout: int = 15
) -> tuple[int, str]:
    """Probe a tenant through the edge from inside the private Docker network.

    In cloudflare_tunnel mode nothing is published on the host, so this is the
    only way to verify the edge route without going out to Cloudflare.
    """
    url = (
        "http://" + layout.edge_container + "/vlux/health?db="
        + urllib.parse.quote(db_name)
    )
    result = docker(
        [
            "run", "--rm", "--network", layout.edge_network,
            "--entrypoint", "curl", probe_image(layout),
            "-fsS", "--max-time", str(timeout), "-H", "Host: " + domain, url,
        ],
        check=False,
        capture=True,
    )
    return result.returncode, (result.stdout or "").strip()


def wait_edge_health(
    layout: "Layout",
    domain: str,
    db_name: str,
    edge_mode: str,
    tls_mode: str,
    timeout: int,
) -> str:
    """Wait for the tenant to answer through the edge, whichever mode is active."""
    if edge_mode != "cloudflare_tunnel":
        return wait_https_health(layout, domain, db_name, tls_mode, timeout)
    holder = {"body": ""}

    def check():
        code, body = edge_probe(layout, domain, db_name)
        holder["body"] = body
        return code == 0 and '"status": "ok"' in body, "last edge probe exit: " + str(code)

    wait_for(check, timeout=timeout, description="edge health for " + domain)
    return holder["body"]


def health_url(domain: str, db_name: str) -> str:
    return "https://" + domain + "/vlux/health?db=" + urllib.parse.quote(db_name)


def wait_https_health(layout: Layout, domain: str, db_name: str, tls_mode: str, timeout: int) -> str:
    context = build_ssl_context(layout, tls_mode)
    url = health_url(domain, db_name)
    holder = {"body": ""}

    def check():
        status, body = http_probe(url, context=context, timeout=10)
        holder["body"] = body
        ok = status == 200 and '"status": "ok"' in body
        return ok, "last HTTPS status: " + str(status)

    wait_for(check, timeout=timeout, description="HTTPS health on " + domain)
    return holder["body"]


# --------------------------------------------------------------------------
# preflight and host-init
# --------------------------------------------------------------------------


def read_os_release() -> dict:
    path = Path("/etc/os-release")
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"')
    return values


def preflight(allow_unsupported_os: bool) -> dict:
    report: dict = {}
    if sys.platform != "linux":
        fail("vlux-cloud targets Linux hosts; detected platform " + sys.platform)

    os_release = read_os_release()
    report["os_id"] = os_release.get("ID", "unknown")
    report["os_version"] = os_release.get("VERSION_ID", "unknown")
    report["os_pretty"] = os_release.get("PRETTY_NAME", "unknown")
    supported = report["os_id"] == "ubuntu" and report["os_version"] == "24.04"
    report["os_supported"] = "PASS" if supported else "UNSUPPORTED"
    if not supported and not allow_unsupported_os:
        fail(
            "Unsupported host OS: "
            + report["os_pretty"]
            + ". VLUX Cloud targets Ubuntu 24.04 LTS. Re-run with --allow-unsupported-os "
            "to override on a lab machine."
        )

    arch = os.uname().machine
    report["architecture"] = arch
    if arch not in {"x86_64", "amd64"}:
        fail("Unsupported architecture " + arch + "; VLUX Cloud images are linux/amd64.")

    docker_version = run(["docker", "version", "--format", "{{.Server.Version}}"], capture=True)
    report["docker_version"] = (docker_version.stdout or "").strip()
    compose_version = run(["docker", "compose", "version", "--short"], capture=True)
    report["docker_compose_version"] = (compose_version.stdout or "").strip()
    if not report["docker_compose_version"].startswith("2"):
        fail(
            "Docker Compose v2 is required; detected "
            + repr(report["docker_compose_version"])
        )
    report["docker"] = "PASS"
    report["docker_compose"] = "PASS"
    return report


def host_port_open(port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def edge_published_ports(layout: "Layout") -> list[str]:
    result = docker(["port", layout.edge_container], check=False, capture=True)
    if result.returncode != 0:
        return []
    return sorted({line.strip() for line in (result.stdout or "").splitlines() if line.strip()})


def edge_reload(layout: Layout) -> None:
    """Validate then hot-reload the edge. Never restarts running tenants."""
    compose(
        layout.edge,
        [
            "exec", "-T", "caddy",
            "caddy", "validate", "--adapter", "caddyfile", "--config", "/etc/caddy/Caddyfile",
        ],
    )
    compose(
        layout.edge,
        [
            "exec", "-T", "caddy",
            "caddy", "reload", "--adapter", "caddyfile", "--config", "/etc/caddy/Caddyfile",
        ],
    )


def write_edge_route(layout: Layout, paths: TenantPaths, content: str) -> None:
    """Install a tenant route atomically and roll it back if the edge rejects it."""
    route = paths.caddy_route
    previous = route.read_text(encoding="utf-8") if route.exists() else None
    write_file(route, content, 0o644)
    try:
        edge_reload(layout)
    except SystemExit:
        if previous is None:
            route.unlink(missing_ok=True)
        else:
            write_file(route, previous, 0o644)
        try:
            edge_reload(layout)
        except SystemExit:
            pass
        raise


def edge_running(layout: Layout) -> bool:
    return container_state(layout.edge_container).get("Status") == "running"


def require_host_init(layout: Layout) -> None:
    if not layout.edge_compose.exists():
        fail("Host is not initialised. Run: sudo vlux-cloud host-init")
    if not edge_running(layout):
        fail("VLUX EDGE is not running. Run: sudo vlux-cloud host-init")


def host_init(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    checks = preflight(args.allow_unsupported_os)

    ensure_dir(layout.base, 0o755)
    ensure_dir(layout.tenants, 0o750)
    ensure_dir(layout.edge, 0o750)
    ensure_dir(layout.edge_tenants, 0o755)
    ensure_dir(layout.edge_data, 0o750)
    ensure_dir(layout.edge_config, 0o750)
    ensure_dir(layout.edge_logs, 0o750)
    ensure_dir(layout.secrets, 0o700)

    edge_mode = args.edge_mode
    if edge_mode not in EDGE_MODES:
        fail("Unknown --edge-mode " + edge_mode)

    acme_email = args.acme_email or ""
    if acme_email:
        validate_email(acme_email)
    acme_email_line = "\temail " + acme_email + "\n" if acme_email else ""
    acme_ca_line = "\tacme_ca " + args.acme_ca + "\n" if args.acme_ca else ""

    if edge_mode == "cloudflare_tunnel":
        # Cloudflare terminates the public TLS, so the edge issues no
        # certificate and therefore needs no ACME account.
        acme_email_line = ""
        acme_ca_line = ""
    caddyfile = render(
        EDGE_TEMPLATE_DIR / "Caddyfile.tmpl",
        {
            "__ACME_EMAIL_LINE__": acme_email_line,
            "__ACME_CA_LINE__": acme_ca_line,
        },
    )
    write_file(layout.edge_caddyfile, caddyfile, 0o644)

    ports_block = ""
    if edge_mode == "public_acme":
        ports_block = "".join(
            [
                "    ports:\n",
                '      - "80:80"\n',
                '      - "443:443"\n',
                '      - "443:443/udp"\n',
            ]
        )
    write_file(
        layout.edge_compose,
        render(
            EDGE_TEMPLATE_DIR / "compose.yaml.tmpl",
            {
                "__PROJECT__": layout.container_prefix + "edge",
                "__EDGE_CONTAINER__": layout.edge_container,
                "__EDGE_NETWORK__": layout.edge_network,
                "__EDGE_ROOT__": str(layout.edge),
                "__EDGE_MODE__": edge_mode,
                "__PORTS_BLOCK__": ports_block,
            },
        ),
        0o640,
    )
    write_file(
        layout.edge_env,
        "\n".join(
            [
                "# Rendered by vlux-cloud host-init. No secrets belong in this file.",
                "VLUX_EDGE_ROOT=" + str(layout.edge),
                "VLUX_CADDY_IMAGE=" + (args.caddy_image or CADDY_IMAGE),
                "VLUX_ACME_EMAIL=" + acme_email,
                "",
            ]
        ),
        0o640,
    )

    if not network_exists(layout.edge_network):
        docker(["network", "create", "--driver", "bridge", layout.edge_network])
        info("Created shared edge network " + layout.edge_network)
    checks["edge_network"] = "PASS"

    compose(layout.edge, ["up", "-d", "--remove-orphans"])
    wait_container_healthy(layout.edge_container, timeout=args.timeout)

    published = edge_published_ports(layout)
    checks["edge_published_ports"] = published
    checks["edge_mode"] = edge_mode
    if edge_mode == "public_acme":
        for port in (80, 443):
            if not host_port_open(port):
                fail("VLUX EDGE is not accepting connections on port " + str(port))
        checks["edge_public_ports"] = "80,443"
    else:
        if published:
            fail(
                "Edge mode cloudflare_tunnel must publish no host port, but Docker "
                "reports: " + ", ".join(published)
            )
        checks["edge_public_ports"] = "NONE"
        checks["host_public_ports"] = "NONE"

    # Filesystem permission contract.
    permission_errors = []
    for path, expected in ((layout.base, 0o755), (layout.edge, 0o750), (layout.tenants, 0o750)):
        actual = os.stat(path).st_mode & 0o777
        if actual != expected:
            permission_errors.append(str(path) + " is " + oct(actual) + ", expected " + oct(expected))
    if permission_errors:
        fail("Filesystem permission check failed: " + "; ".join(permission_errors))
    checks["filesystem_permissions"] = "PASS"

    state = {
        "cloud_version": CLOUD_VERSION,
        "base_dir": str(layout.base),
        "host_id": layout.host_id,
        "edge_mode": edge_mode,
        "deployment_class": (
            "CLOUDFLARE_TEMPORARY_STAGING" if edge_mode == "cloudflare_tunnel"
            else "CLOUD_MANAGED_VPS"
        ),
        "edge_network": layout.edge_network,
        "edge_public_ports": (
            ["80/tcp", "443/tcp", "443/udp"] if edge_mode == "public_acme" else []
        ),
        "caddy_image": args.caddy_image or CADDY_IMAGE,
        "acme_email": acme_email or None,
        "acme_ca": args.acme_ca or None,
        "initialised_at": utc_iso(),
        "preflight": checks,
    }
    write_json(layout.host_state, state, mode=0o640)

    payload = dict(state)
    payload["edge_health"] = container_health(layout.edge_container)
    payload["tenants"] = layout.known_tenants()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


# --------------------------------------------------------------------------
# provisioning
# --------------------------------------------------------------------------


def psql(paths: TenantPaths, sql: str, *, database: str | None = None, check: bool = True):
    metadata = read_json(paths.metadata)
    db_user = metadata.get("database", {}).get("role") or db_identifier(paths.name)
    target = database or metadata.get("database", {}).get("name") or db_identifier(paths.name)
    return compose(
        paths.root,
        [
            "exec", "-T", "postgres",
            "psql", "-v", "ON_ERROR_STOP=1", "-U", db_user, "-d", target, "-tAc", sql,
        ],
        check=check,
        capture=True,
    )


def database_initialised(paths: TenantPaths) -> bool:
    result = psql(paths, "SELECT to_regclass('public.ir_module_module') IS NOT NULL", check=False)
    return result.returncode == 0 and (result.stdout or "").strip() == "t"


def odoo_run(paths: TenantPaths, args: list[str], *, input_text: str | None = None, timeout: int = 3600) -> None:
    one_off = paths.project + "-oneoff-" + utc_stamp()
    compose(
        paths.root,
        ["run", "--rm", "--name", one_off, "-T", "app", *args],
        input_text=input_text,
        timeout=timeout,
    )


def install_addons(paths: TenantPaths, db_name: str) -> None:
    modules = ",".join(PRODUCTIVE_ADDONS)
    info("Initialising database and installing " + modules)
    odoo_run(
        paths,
        [
            "-d", db_name,
            "-i", modules,
            "--stop-after-init",
            "--without-demo=all",
            "--no-http",
        ],
    )


def upgrade_addons(paths: TenantPaths, db_name: str) -> None:
    modules = ",".join(PRODUCTIVE_ADDONS)
    info("Upgrading targeted modules only: " + modules)
    odoo_run(
        paths,
        ["-d", db_name, "-u", modules, "--stop-after-init", "--no-http"],
    )


def create_owner(paths: TenantPaths, db_name: str, owner_email: str, company_name: str | None) -> None:
    """Replace the default admin login with a named Owner and a random password.

    The password is passed on stdin only: it never reaches argv, the process
    table, container logs or CI output.
    """
    password = paths.secret("initial_owner_password")
    company_line = ""
    if company_name:
        company_line = (
            "company = env.ref('base.main_company')\n"
            "company.sudo().write({'name': " + repr(company_name) + "})\n"
        )
    script = (
        "user = env.ref('base.user_admin')\n"
        "group = env.ref(" + repr(OWNER_GROUP_XMLID) + ")\n"
        "user.sudo().write({\n"
        "    'login': " + repr(owner_email) + ",\n"
        "    'name': 'VLUX Owner',\n"
        "    'email': " + repr(owner_email) + ",\n"
        "    'password': " + repr(password) + ",\n"
        "    'group_ids': [(4, group.id)],\n"
        "})\n"
        + company_line
        + "assert user.sudo().has_group(" + repr(OWNER_GROUP_XMLID) + "), 'owner group not applied'\n"
        "env.cr.commit()\n"
    )
    info("Creating initial Owner user (credentials stay in root-only tenant secrets)")
    odoo_run(paths, ["shell", "-d", db_name, "--no-http"], input_text=script, timeout=900)


def tenant_env_file(
    paths: TenantPaths,
    db_name: str,
    db_user: str,
    app_image: str,
    postgres_image: str,
) -> None:
    write_file(
        paths.env_file,
        "\n".join(
            [
                "# Rendered by vlux-cloud. Contains no secrets: the database password",
                "# is a Docker secret file and the Odoo master password lives in",
                "# " + str(paths.secrets) + " (root-only, 0600).",
                "VLUX_TENANT=" + paths.name,
                "VLUX_DB_NAME=" + db_name,
                "VLUX_DB_USER=" + db_user,
                "VLUX_APP_IMAGE=" + app_image,
                "VLUX_POSTGRES_IMAGE=" + postgres_image,
                "",
            ]
        ),
        0o640,
    )


def render_tenant_files(
    paths: TenantPaths,
    *,
    db_name: str,
    db_user: str,
    db_host: str,
    workers: int,
    max_cron_threads: int,
    memory_soft_mb: int,
    memory_hard_mb: int,
    readonly_rootfs: bool,
) -> None:
    read_only_block = ""
    if readonly_rootfs:
        read_only_block = (
            "    read_only: true\n"
            "    tmpfs:\n"
            "      - /tmp:size=512m,mode=1777\n"
        )
    write_file(
        paths.compose,
        render(
            TEMPLATE_DIR / "tenant-compose.yaml.tmpl",
            {
                "__TENANT_ROOT__": str(paths.root),
                "__PROJECT__": paths.project,
                "__PG_CONTAINER__": paths.pg_container,
                "__APP_CONTAINER__": paths.app_container,
                "__APP_ALIAS__": paths.app_alias,
                "__PRIVATE_NETWORK__": paths.private_network,
                "__EDGE_NETWORK__": paths.layout.edge_network,
                "__TENANT__": paths.name,
                "__READ_ONLY_BLOCK__": read_only_block,
            },
        ),
        0o640,
    )

    db_maxconn = max(16, (workers + max_cron_threads) * 8)
    write_file(
        paths.odoo_conf,
        render(
            TEMPLATE_DIR / "odoo.conf.tmpl",
            {
                "__VLUX_ADMIN_PASSWD__": paths.secret("admin_passwd"),
                "__DB_HOST__": db_host,
                "__DB_PORT__": str(DB_PORT),
                "__DB_USER__": db_user,
                "__VLUX_DB_PASSWORD__": paths.secret("db_password"),
                "__DB_NAME__": db_name,
                "__DB_MAXCONN__": str(db_maxconn),
                "__HTTP_PORT__": str(HTTP_PORT),
                "__GEVENT_PORT__": str(GEVENT_PORT),
                "__WORKERS__": str(workers),
                "__MAX_CRON_THREADS__": str(max_cron_threads),
                "__LIMIT_MEMORY_SOFT__": str(memory_soft_mb * 1024 * 1024),
                "__LIMIT_MEMORY_HARD__": str(memory_hard_mb * 1024 * 1024),
            },
        ),
        0o640,
        uid=APP_UID,
        gid=APP_GID,
    )


def route_site_address(domain: str, edge_mode: str, tls_mode: str) -> tuple[str, str]:
    """Return (site address, tls line) for a tenant route.

    Behind a Cloudflare Tunnel the edge serves plain HTTP on the private Docker
    network, so the site carries an explicit http:// scheme and Caddy never
    tries to obtain a certificate: Cloudflare terminates the public TLS.
    """
    if edge_mode == "cloudflare_tunnel":
        return "http://" + domain, ""
    return domain, (TLS_INTERNAL_LINE if tls_mode == "internal" else "")


def tenant_route_content(
    paths: TenantPaths,
    domain: str,
    tls_mode: str,
    edge_mode: str,
    maintenance: bool = False,
) -> str:
    site_address, tls_line = route_site_address(domain, edge_mode, tls_mode)
    template = "tenant-maintenance.caddy.tmpl" if maintenance else "tenant.caddy.tmpl"
    return render(
        TEMPLATE_DIR / template,
        {
            "__SITE_ADDRESS__": site_address,
            "__TLS_LINE__": tls_line,
            "__APP_ALIAS__": paths.app_alias,
            "__TENANT__": paths.name,
            "__HTTP_PORT__": str(HTTP_PORT),
            "__GEVENT_PORT__": str(GEVENT_PORT),
        },
    )


def provision(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    require_host_init(layout)

    tenant = validate_tenant(args.tenant)
    domain = validate_domain(args.domain)
    owner_email = validate_email(args.owner_email)
    if args.tls_mode not in TLS_MODES:
        fail("Unknown --tls-mode " + args.tls_mode)

    paths = layout.tenant(tenant)
    existing = read_json(paths.metadata)
    idempotent_run = bool(existing)

    for other in layout.known_tenants():
        if other == tenant:
            continue
        other_meta = read_json(layout.tenant(other).metadata)
        if other_meta.get("domain") == domain:
            fail("Domain " + domain + " is already routed to tenant " + other)

    if idempotent_run:
        info("Tenant " + tenant + " already exists: reconciling without destroying data.")
        if existing.get("domain") != domain:
            fail(
                "Tenant "
                + tenant
                + " is provisioned for "
                + str(existing.get("domain"))
                + ". Domain changes are a separate, deliberate operation."
            )

    dns = dns_preflight(domain, args.tls_mode, args.skip_dns_check)

    pg_uid, pg_gid = postgres_account(args.postgres_image or POSTGRES_IMAGE)
    ensure_dir(paths.root, 0o750)
    ensure_dir(paths.secrets, 0o700)
    ensure_dir(paths.backups, 0o700)
    ensure_dir(paths.postgres, 0o700, uid=pg_uid, gid=pg_gid)
    ensure_dir(paths.config, 0o750, uid=APP_UID, gid=APP_GID)
    ensure_dir(paths.filestore, 0o750, uid=APP_UID, gid=APP_GID)
    ensure_dir(paths.logs, 0o750, uid=APP_UID, gid=APP_GID)

    # Secrets are generated once. Re-provisioning never rotates them.
    generated = []
    for name, length in (
        ("db_password", 32),
        ("admin_passwd", 36),
        ("initial_owner_password", 24),
    ):
        if not paths.has_secret(name):
            paths.write_secret(name, secrets.token_urlsafe(length))
            generated.append(name)
    if generated:
        info("Generated tenant secrets: " + ", ".join(generated))
    else:
        info("Existing tenant secrets preserved.")

    db_name = existing.get("database", {}).get("name") or db_identifier(tenant)
    db_user = existing.get("database", {}).get("role") or db_identifier(tenant)
    db_host = args.db_host or existing.get("database", {}).get("host") or "postgres"
    app_image = args.image or existing.get("image", {}).get("reference") or DEFAULT_APP_IMAGE
    postgres_image = args.postgres_image or POSTGRES_IMAGE
    workers = args.workers if args.workers is not None else existing.get("odoo", {}).get("workers", DEFAULT_WORKERS)
    max_cron = (
        args.max_cron_threads
        if args.max_cron_threads is not None
        else existing.get("odoo", {}).get("max_cron_threads", DEFAULT_MAX_CRON_THREADS)
    )
    if workers < 0 or workers > 32:
        fail("--workers must be between 0 and 32")
    if max_cron < 0 or max_cron > 8:
        fail("--max-cron-threads must be between 0 and 8")

    tenant_env_file(paths, db_name, db_user, app_image, postgres_image)
    render_tenant_files(
        paths,
        db_name=db_name,
        db_user=db_user,
        db_host=db_host,
        workers=workers,
        max_cron_threads=max_cron,
        memory_soft_mb=args.limit_memory_soft_mb,
        memory_hard_mb=args.limit_memory_hard_mb,
        readonly_rootfs=not args.no_readonly_rootfs,
    )

    if args.pull:
        docker(["pull", app_image])
        docker(["pull", postgres_image])

    info("Starting isolated PostgreSQL for " + tenant)
    compose(paths.root, ["up", "-d", "postgres"])
    wait_container_healthy(paths.pg_container, timeout=args.timeout)

    first_init = not database_initialised(paths)
    if first_init:
        install_addons(paths, db_name)
        create_owner(paths, db_name, owner_email, args.company_name)
    else:
        info("Database already initialised: skipping module install and Owner creation.")

    info("Starting Odoo application container")
    compose(paths.root, ["up", "-d", "app"])
    wait_container_healthy(paths.app_container, timeout=args.timeout)

    edge_mode = layout.edge_mode()
    write_edge_route(
        layout, paths, tenant_route_content(paths, domain, args.tls_mode, edge_mode)
    )
    info("Edge route installed and Caddy reloaded (other tenants untouched)")

    host_state = read_json(layout.host_state)
    if host_state and host_state.get("probe_image") != app_image:
        host_state["probe_image"] = app_image
        write_json(layout.host_state, host_state, mode=0o640)

    https_status = "PENDING"
    https_detail = ""
    try:
        wait_edge_health(
            layout, domain, db_name, edge_mode, args.tls_mode, timeout=args.https_timeout
        )
        https_status = "PASS"
    except SystemExit as exc:
        https_detail = getattr(exc, "message", str(exc))
        if edge_mode == "cloudflare_tunnel":
            raise
        if args.tls_mode == "public":
            https_status = "MANUAL_PENDING"
        else:
            raise

    metadata = {
        "tenant": tenant,
        "domain": domain,
        "edition": args.edition,
        "data_class": args.data_class,
        "cloud_version": CLOUD_VERSION,
        "tls_mode": args.tls_mode,
        "dns_preflight": dns,
        "owner_email": owner_email,
        "created_at": existing.get("created_at") or utc_iso(),
        "updated_at": utc_iso(),
        "database": {
            "engine": CLOUD_DB_ENGINE,
            "major": CLOUD_DB_MAJOR,
            "target": CLOUD_DB_TARGET,
            "name": db_name,
            "role": db_user,
            "host": db_host,
            "port": DB_PORT,
            "public_exposure": "NONE",
        },
        "image": {
            "reference": app_image,
            "digest": image_digest(app_image),
            "postgres": postgres_image,
            "postgres_amd64_digest": POSTGRES_AMD64_DIGEST,
            "odoo_commit": ODOO_COMMIT,
            "python_version": PYTHON_VERSION,
        },
        "odoo": {
            "workers": workers,
            "max_cron_threads": max_cron,
            "http_port": HTTP_PORT,
            "gevent_port": GEVENT_PORT,
            "proxy_mode": True,
            "list_db": False,
            "addons": list(PRODUCTIVE_ADDONS),
        },
        "networks": {
            "private": paths.private_network,
            "edge": layout.edge_network,
            "app_alias": paths.app_alias,
        },
        "paths": {
            "root": str(paths.root),
            "filestore": str(paths.filestore),
            "logs": str(paths.logs),
            "backups": str(paths.backups),
            "secrets": str(paths.secrets),
        },
        "secrets_present": sorted(
            name for name in ("db_password", "admin_passwd", "initial_owner_password")
            if paths.has_secret(name)
        ),
        "readonly_rootfs": not args.no_readonly_rootfs,
        "backup": existing.get("backup", {"last_success": None, "last_backup_id": None}),
        "upgrade_history": existing.get("upgrade_history", []),
        "state": "ACTIVE",
    }
    write_json(paths.metadata, metadata, mode=0o640)

    summary = {
        "tenant": tenant,
        "domain": domain,
        "url": "https://" + domain,
        "tls_mode": args.tls_mode,
        "https_health": https_status,
        "database": db_name,
        "db_role": db_user,
        "postgres_public_exposure": "NONE",
        "app_alias": tenant + "-app",
        "private_network": paths.private_network,
        "workers": workers,
        "max_cron_threads": max_cron,
        "image": app_image,
        "image_digest": metadata["image"]["digest"],
        "owner_email": owner_email,
        "owner_credential": "stored root-only at " + str(paths.secrets / "initial_owner_password"),
        "provision_mode": "RECONCILED" if idempotent_run else "CREATED",
        "secrets_rotated": bool(generated) and not idempotent_run,
    }
    if https_detail:
        summary["https_detail"] = https_detail
    print(json.dumps(summary, indent=2, sort_keys=True))
    if https_status == "MANUAL_PENDING":
        info(
            "Public ACME HTTPS could not be confirmed from this host. Verify DNS and "
            "re-run `vlux-cloud health " + tenant + "` once the record propagates."
        )
    return 0


# --------------------------------------------------------------------------
# status, health, list
# --------------------------------------------------------------------------


def tenant_health_report(layout: Layout, paths: TenantPaths) -> dict:
    metadata = paths.require()
    db_name = metadata["database"]["name"]
    domain = metadata["domain"]
    tls_mode = metadata.get("tls_mode", "public")

    report: dict = {"tenant": paths.name, "domain": domain}

    pg_container = paths.pg_container
    app_container = paths.app_container
    report["postgres_container"] = container_health(pg_container)
    report["app_container"] = container_health(app_container)

    pg = compose(
        paths.root,
        ["exec", "-T", "postgres", "pg_isready", "-h", "127.0.0.1", "-U", metadata["database"]["role"], "-d", db_name],
        check=False,
        capture=True,
    )
    report["postgres"] = "PASS" if pg.returncode == 0 else "FAIL"

    app_probe = compose(
        paths.root,
        [
            "exec", "-T", "app",
            "curl", "-fsS", "http://127.0.0.1:" + str(HTTP_PORT) + "/vlux/health?db=" + db_name,
        ],
        check=False,
        capture=True,
    )
    report["odoo_health_endpoint"] = (
        "PASS" if app_probe.returncode == 0 and '"status": "ok"' in (app_probe.stdout or "") else "FAIL"
    )

    report["edge_route"] = "PASS" if paths.caddy_route.exists() else "MISSING"
    report["edge_container"] = container_health(layout.edge_container)

    edge_mode = layout.edge_mode()
    report["edge_mode"] = edge_mode
    if edge_mode == "cloudflare_tunnel":
        # Cloudflare terminates the public TLS, so from the host the only
        # meaningful check is the edge route itself over the private network.
        code, body = edge_probe(layout, domain, db_name)
        report["edge_http_exit"] = code
        report["https"] = "PASS" if code == 0 and '"status": "ok"' in body else "FAIL"
        report["public_https"] = "MANUAL_PENDING_CLOUDFLARE"
    else:
        status, body = http_probe(
            health_url(domain, db_name), context=build_ssl_context(layout, tls_mode), timeout=15
        )
        report["https_status_code"] = status
        report["https"] = "PASS" if status == 200 and '"status": "ok"' in body else "FAIL"

    critical = [
        report["postgres"],
        report["odoo_health_endpoint"],
        report["edge_route"],
        report["https"],
    ]
    report["status"] = "ok" if all(value == "PASS" for value in critical) else "degraded"
    return report


def health(args: argparse.Namespace) -> int:
    layout = Layout.load(Path(args.base_dir), args.host_id)
    targets = [args.tenant] if args.tenant else layout.known_tenants()
    if not targets:
        fail("No tenants provisioned on this host.")
    reports = [tenant_health_report(layout, layout.tenant(name)) for name in targets]
    payload = {"checked_at": utc_iso(), "tenants": reports}
    payload["status"] = "ok" if all(item["status"] == "ok" for item in reports) else "degraded"
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "ok" else 2


def backup_protection(layout: Layout, paths: TenantPaths, metadata: dict) -> dict:
    """Judge whether this tenant is actually protected, not just configured.

    Real client data on a temporary staging host is the case this exists for:
    it warns loudly without ever blocking the tenant.
    """
    data_class = metadata.get("data_class", DEFAULT_DATA_CLASS)
    backup_state = metadata.get("backup", {}) or {}
    reasons: list[str] = []

    last_success = backup_state.get("last_success")
    if not last_success:
        reasons.append("no successful backup has been recorded")
    else:
        try:
            taken = _dt.datetime.fromisoformat(last_success)
            age_hours = (utc_now() - taken).total_seconds() / 3600.0
            if age_hours > 48:
                reasons.append("last backup is " + str(int(age_hours)) + "h old")
        except ValueError:
            reasons.append("last backup timestamp is unreadable")

    configured = offsite_config(layout) is not None
    if not configured:
        reasons.append("no off-site storage is configured")
    elif not backup_state.get("offsite_last_success"):
        reasons.append("no off-site upload has succeeded")

    local_copies = len(backup_archives(paths))
    if local_copies == 0:
        reasons.append("no local backup archive is present")

    status_value = "OK" if not reasons else "DEGRADED"
    report = {
        "status": status_value,
        "data_class": data_class,
        "offsite_configured": configured,
        "local_archives": local_copies,
        "last_backup": last_success,
        "last_offsite_backup": backup_state.get("offsite_last_success"),
        "reasons": reasons,
    }
    if data_class == "REAL_CLIENT_DATA" and reasons:
        report["warning"] = (
            "This tenant holds real client data and is not fully protected: "
            + "; ".join(reasons)
        )
    return report


def status(args: argparse.Namespace) -> int:
    layout = Layout.load(Path(args.base_dir), args.host_id)
    targets = [args.tenant] if args.tenant else layout.known_tenants()
    if not targets:
        fail("No tenants provisioned on this host.")
    rows = []
    for name in targets:
        paths = layout.tenant(name)
        metadata = paths.require()
        db_name = metadata["database"]["name"]
        health_report = tenant_health_report(layout, paths)
        rows.append(
            {
                "tenant": name,
                "domain": metadata["domain"],
                "url": "https://" + metadata["domain"],
                "state": metadata.get("state", "ACTIVE"),
                "edition": metadata.get("edition"),
                "cloud_version": metadata.get("cloud_version"),
                "version": metadata.get("image", {}).get("reference"),
                "image_digest": metadata.get("image", {}).get("digest"),
                "odoo_commit": metadata.get("image", {}).get("odoo_commit"),
                "odoo": health_report["app_container"],
                "odoo_workers": metadata.get("odoo", {}).get("workers"),
                "postgresql": health_report["postgres_container"],
                "postgres_image": metadata.get("image", {}).get("postgres"),
                "postgres_public_exposure": "NONE",
                "database": db_name,
                "db_role": metadata["database"]["role"],
                "https": health_report["https"],
                "health": health_report["status"],
                "data_class": metadata.get("data_class", DEFAULT_DATA_CLASS),
                "maintenance": bool(metadata.get("maintenance", {}).get("enabled")),
                "backup_protection": backup_protection(layout, paths, metadata),
                "backup_last_success": metadata.get("backup", {}).get("last_success"),
                "backup_last_id": metadata.get("backup", {}).get("last_backup_id"),
                "backup_offsite_last_success": metadata.get("backup", {}).get("offsite_last_success"),
                "disk_usage_mb": {
                    "total": round(du_bytes(paths.root) / (1024 * 1024), 2),
                    "postgres": round(du_bytes(paths.postgres) / (1024 * 1024), 2),
                    "filestore": round(du_bytes(paths.filestore) / (1024 * 1024), 2),
                    "backups": round(du_bytes(paths.backups) / (1024 * 1024), 2),
                },
                "secrets_present": metadata.get("secrets_present", []),
            }
        )
    host_state = read_json(layout.host_state)
    edge_mode = host_state.get("edge_mode", DEFAULT_EDGE_MODE)
    host_block = {
        "base_dir": str(layout.base),
        "host_id": layout.host_id or "default",
        "edge": container_health(layout.edge_container),
        "edge_mode": edge_mode,
        "edge_public_ports": edge_published_ports(layout),
        "deployment_class": host_state.get(
            "deployment_class",
            "CLOUDFLARE_TEMPORARY_STAGING" if edge_mode == "cloudflare_tunnel"
            else "CLOUD_MANAGED_VPS",
        ),
        "cloud_version": CLOUD_VERSION,
    }
    if edge_mode == "cloudflare_tunnel":
        host_block["host_public_ports"] = (
            "NONE" if not host_block["edge_public_ports"] else "UNEXPECTED"
        )
        host_block["tunnel"] = tunnel_state(layout)
    payload = {
        "host": host_block,
        "tenants": rows,
        "backup_protection": (
            "DEGRADED"
            if any(row["backup_protection"]["status"] != "OK" for row in rows)
            else "OK"
        ),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def list_tenants(args: argparse.Namespace) -> int:
    layout = Layout.load(Path(args.base_dir), args.host_id)
    rows = []
    for name in layout.known_tenants():
        metadata = read_json(layout.tenant(name).metadata)
        rows.append(
            {
                "tenant": name,
                "domain": metadata.get("domain"),
                "state": metadata.get("state", "ACTIVE"),
                "tls_mode": metadata.get("tls_mode"),
                "database": metadata.get("database", {}).get("name"),
                "image": metadata.get("image", {}).get("reference"),
                "created_at": metadata.get("created_at"),
            }
        )
    print(json.dumps({"tenants": rows, "count": len(rows)}, indent=2, sort_keys=True))
    return 0


# --------------------------------------------------------------------------
# S3-compatible off-site storage (AWS S3, Cloudflare R2, Backblaze B2, MinIO)
# --------------------------------------------------------------------------


def offsite_config(layout: Layout) -> dict | None:
    """Assemble off-site settings from the root-only config file and the env.

    Credentials never live in this repository, in the image or in tenant.json.
    """
    config = read_json(layout.offsite_config)
    env_map = {
        "endpoint": "VLUX_OFFSITE_ENDPOINT",
        "region": "VLUX_OFFSITE_REGION",
        "bucket": "VLUX_OFFSITE_BUCKET",
        "prefix": "VLUX_OFFSITE_PREFIX",
        "access_key_id": "VLUX_OFFSITE_ACCESS_KEY_ID",
        "secret_access_key": "VLUX_OFFSITE_SECRET_ACCESS_KEY",
        "session_token": "VLUX_OFFSITE_SESSION_TOKEN",
    }
    for key, env_name in env_map.items():
        value = os.environ.get(env_name)
        if value:
            config[key] = value
    # Credentials are preferably stored as root-only files rather than inline,
    # so `vlux-cloud r2 configure` records the paths and they are read here.
    for key, file_key in (
        ("access_key_id", "access_key_id_file"),
        ("secret_access_key", "secret_access_key_file"),
        ("session_token", "session_token_file"),
    ):
        if config.get(key):
            continue
        reference = config.get(file_key)
        if not reference:
            continue
        path = Path(reference)
        if not path.is_file():
            fail("Configured credential file is missing: " + str(path))
        config[key] = path.read_text(encoding="utf-8").strip()
    required = ("endpoint", "bucket", "access_key_id", "secret_access_key")
    if not all(config.get(key) for key in required):
        return None
    config.setdefault("region", "us-east-1")
    config.setdefault("prefix", "vlux-pos")
    return config


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _s3_signed_request(
    config: dict,
    method: str,
    object_key: str,
    *,
    payload_sha256: str,
    body=None,
    content_length: int | None = None,
    query: dict[str, str] | None = None,
) -> tuple[urllib.request.Request, dict]:
    """Build a SigV4-signed request for any S3-compatible endpoint.

    Path-style addressing is used because it works uniformly across AWS S3,
    Cloudflare R2, Backblaze B2 and MinIO.
    """
    endpoint = str(config["endpoint"]).rstrip("/")
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"}:
        fail("Off-site endpoint must be an http(s) URL: " + endpoint)
    host = parsed.netloc
    segments = [config["bucket"], *object_key.split("/")]
    canonical_uri = "/" + "/".join(urllib.parse.quote(part, safe="") for part in segments if part != "")
    canonical_query = "&".join(
        urllib.parse.quote(name, safe="") + "=" + urllib.parse.quote(str(value), safe="")
        for name, value in sorted((query or {}).items())
    )
    url = endpoint + canonical_uri + (("?" + canonical_query) if canonical_query else "")

    now = utc_now()
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    region = config["region"]

    signed_map = {
        "host": host,
        "x-amz-content-sha256": payload_sha256,
        "x-amz-date": amz_date,
    }
    if config.get("session_token"):
        signed_map["x-amz-security-token"] = config["session_token"]
    signed_headers = ";".join(sorted(signed_map))
    canonical_headers = "".join(name + ":" + signed_map[name] + "\n" for name in sorted(signed_map))
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_sha256]
    )
    scope = datestamp + "/" + region + "/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    key = _sign(("AWS4" + config["secret_access_key"]).encode("utf-8"), datestamp)
    key = _sign(key, region)
    key = _sign(key, "s3")
    key = _sign(key, "aws4_request")
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {
        "x-amz-content-sha256": payload_sha256,
        "x-amz-date": amz_date,
        "Authorization": (
            "AWS4-HMAC-SHA256 Credential="
            + config["access_key_id"]
            + "/"
            + scope
            + ", SignedHeaders="
            + signed_headers
            + ", Signature="
            + signature
        ),
    }
    if config.get("session_token"):
        headers["x-amz-security-token"] = config["session_token"]
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
        headers["Content-Type"] = "application/octet-stream"

    return urllib.request.Request(url, data=body, method=method, headers=headers), headers


def _s3_request(
    config: dict,
    method: str,
    object_key: str,
    *,
    payload_sha256: str,
    body=None,
    content_length: int | None = None,
    query: dict[str, str] | None = None,
    timeout: int = 300,
) -> tuple[int, dict, bytes]:
    request, _headers = _s3_signed_request(
        config,
        method,
        object_key,
        payload_sha256=payload_sha256,
        body=body,
        content_length=content_length,
        query=query,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read(65536)
    except Exception as exc:  # noqa: BLE001
        return 0, {}, str(exc).encode("utf-8")


def offsite_object_key(config: dict, tenant: str, archive: Path, tier: str = "daily") -> str:
    """tenants/<tenant>/<tier>/<archive> under the configured prefix."""
    return "/".join(
        [str(config["prefix"]).strip("/"), "tenants", tenant, tier, archive.name]
    )


def offsite_upload(
    config: dict, archive: Path, tenant: str, tier: str = "daily"
) -> dict:
    object_key = offsite_object_key(config, tenant, archive, tier)
    digest = sha256_file(archive)
    size = archive.stat().st_size
    multipart = None

    if size >= MULTIPART_THRESHOLD:
        multipart = _s3_multipart_upload(config, archive, object_key)
    else:
        with archive.open("rb") as stream:
            status, _headers, body = _s3_request(
                config,
                "PUT",
                object_key,
                payload_sha256=digest,
                body=stream,
                content_length=size,
            )
        if status not in (200, 201):
            fail(
                "Off-site upload failed with status "
                + str(status)
                + ": "
                + body.decode("utf-8", "replace")[:400]
            )

    # A 200 from the upload is not proof: confirm the object is really there
    # and the stored size matches before recording an off-site success.
    remote_size = s3_object_size(config, object_key)
    if remote_size is None:
        fail("Off-site verification failed: " + object_key + " is not readable after upload.")
    if remote_size != size:
        fail(
            "Off-site verification failed for " + object_key + ": stored "
            + str(remote_size) + " bytes, expected " + str(size)
        )
    return {
        "status": "PASS",
        "bucket": config["bucket"],
        "object_key": object_key,
        "tier": tier,
        "endpoint_host": urllib.parse.urlsplit(str(config["endpoint"])).netloc,
        "bytes": size,
        "sha256": digest,
        "multipart": multipart,
        "uploaded_at": utc_iso(),
    }


# --------------------------------------------------------------------------
# backup and restore
# --------------------------------------------------------------------------


def app_is_running(paths: "TenantPaths") -> bool:
    return container_state(paths.app_container).get("Status") == "running"


def stop_app(paths: TenantPaths) -> None:
    compose(paths.root, ["stop", "app"], timeout=300)


def start_app(paths: TenantPaths, timeout: int) -> None:
    compose(paths.root, ["up", "-d", "app"])
    wait_container_healthy(paths.app_container, timeout=timeout)


def dump_database(paths: TenantPaths, metadata: dict, target: Path) -> None:
    run(
        [
            "docker", "exec", "-i", paths.pg_container,
            "pg_dump",
            "-U", metadata["database"]["role"],
            "-d", metadata["database"]["name"],
            "--format=custom",
            "--compress=6",
            "--no-owner",
            "--no-privileges",
        ],
        stdout_path=target,
        timeout=7200,
    )
    if not target.exists() or target.stat().st_size == 0:
        fail("pg_dump produced an empty dump for tenant " + paths.name)


def create_backup(
    layout: Layout,
    paths: TenantPaths,
    *,
    label: str,
    offsite: bool,
    timeout: int,
    tier: str = "daily",
    migration: bool = False,
    extra_manifest: dict | None = None,
) -> dict:
    metadata = paths.require()
    backup_id = paths.name + "-" + label + "-" + utc_stamp()
    staging = paths.backups / backup_id
    if staging.exists():
        fail("Backup staging directory already exists: " + str(staging))
    ensure_dir(staging, 0o700)

    was_running = app_is_running(paths)
    if was_running:
        info("Quiescing the app container so the dump and filestore snapshot agree")
        stop_app(paths)
    try:
        dump_database(paths, metadata, staging / "database.dump")
        run(
            [
                "tar", "--numeric-owner", "-czf", str(staging / "filestore.tar.gz"),
                "-C", str(paths.root), "filestore",
            ],
            timeout=7200,
        )
        # A migration bundle must be safe to move between hosts, so every
        # host-specific key is dropped as well as anything secret-adjacent.
        drop = {"secrets_present"}
        if migration:
            drop |= {"paths", "networks", "image", "backup", "upgrade_history", "maintenance"}
        sanitized = {key: value for key, value in metadata.items() if key not in drop}
        write_json(staging / "tenant.json", sanitized, mode=0o600)
    finally:
        if was_running:
            start_app(paths, timeout)

    files = {}
    for name in ("database.dump", "filestore.tar.gz", "tenant.json"):
        path = staging / name
        files[name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}

    manifest = {
        "backup_id": backup_id,
        "backup_format": "vlux-cloud/1",
        "tenant": paths.name,
        "domain": metadata["domain"],
        "created_at": utc_iso(),
        "cloud_version": CLOUD_VERSION,
        "label": label,
        "database": {
            "engine": CLOUD_DB_ENGINE,
            "major": CLOUD_DB_MAJOR,
            "name": metadata["database"]["name"],
            "role": metadata["database"]["role"],
            "dump_format": "pg_dump custom (-Fc, compressed)",
        },
        "image": {
            "reference": metadata["image"]["reference"],
            "digest": metadata["image"]["digest"],
            "odoo_commit": metadata["image"]["odoo_commit"],
        },
        "consistency": {
            "app_quiesced": was_running,
            "method": "app container stopped for the whole dump + filestore snapshot",
        },
        "excluded": [
            "db_password",
            "admin_passwd",
            "initial_owner_password",
            "cloudflare tunnel token",
            "R2/S3 credentials",
            "tls private keys",
            "host credentials",
        ],
        "files": files,
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    if extra_manifest and "metrics" in extra_manifest:
        write_json(staging / "metrics.json", extra_manifest["metrics"], mode=0o600)
        metrics_path = staging / "metrics.json"
        files["metrics.json"] = {
            "sha256": sha256_file(metrics_path),
            "bytes": metrics_path.stat().st_size,
        }
        manifest["files"] = files
    write_json(staging / "manifest.json", manifest, mode=0o600)
    checksums = "".join(
        files[name]["sha256"] + "  " + name + "\n" for name in sorted(files)
    )
    write_file(staging / "SHA256SUMS", checksums, 0o600)

    archive = paths.backups / (backup_id + ".tar.gz")
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(staging, arcname=backup_id)
    shutil.rmtree(staging)
    os.chmod(archive, 0o600)

    record = {
        "backup_id": backup_id,
        "archive": str(archive),
        "sha256": sha256_file(archive),
        "bytes": archive.stat().st_size,
        "created_at": manifest["created_at"],
        "consistency": manifest["consistency"],
        "local_backup": "PASS",
    }
    write_file(
        paths.backups / (backup_id + ".tar.gz.sha256"),
        record["sha256"] + "  " + archive.name + "\n",
        0o600,
    )

    if offsite:
        config = offsite_config(layout)
        if not config:
            fail(
                "Off-site backup requested but no S3-compatible configuration found. "
                "Populate " + str(layout.offsite_config) + " (root-only, 0600) or export "
                "VLUX_OFFSITE_ENDPOINT / _BUCKET / _ACCESS_KEY_ID / _SECRET_ACCESS_KEY."
            )
        record["offsite"] = offsite_upload(config, archive, paths.name, tier)
    else:
        record["offsite"] = {"status": "NOT_REQUESTED"}

    metadata.setdefault("backup", {})
    metadata["backup"]["last_success"] = record["created_at"]
    metadata["backup"]["last_backup_id"] = backup_id
    metadata["backup"]["last_archive"] = str(archive)
    metadata["backup"]["last_sha256"] = record["sha256"]
    if record["offsite"].get("status") == "PASS":
        metadata["backup"]["offsite_last_success"] = record["offsite"]["uploaded_at"]
        metadata["backup"]["offsite_last_object"] = record["offsite"]["object_key"]
    write_json(paths.metadata, metadata, mode=0o640)
    return record


def backup(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    paths = layout.tenant(args.tenant)
    paths.require()
    record = create_backup(
        layout,
        paths,
        label=args.label,
        offsite=args.offsite,
        timeout=args.timeout,
        tier=args.tier,
    )
    if args.retention:
        # Only ever prune after the new archive exists and verified, so the
        # policy can never remove the last good backup.
        record["retention"] = apply_retention(
            paths,
            keep_daily=args.keep_daily,
            keep_weekly=args.keep_weekly,
            keep_monthly=args.keep_monthly,
        )
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def retention_command(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    paths = layout.tenant(args.tenant)
    paths.require()
    result = apply_retention(
        paths,
        keep_daily=args.keep_daily,
        keep_weekly=args.keep_weekly,
        keep_monthly=args.keep_monthly,
        dry_run=args.dry_run,
    )
    result["tenant"] = paths.name
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def safe_extract(archive: tarfile.TarFile, target: Path) -> None:
    root = target.resolve()
    for member in archive.getmembers():
        if member.issym() or member.islnk():
            fail("Backup archive contains a link member: " + member.name)
        resolved = (target / member.name).resolve()
        if not str(resolved).startswith(str(root)):
            fail("Unsafe path in backup archive: " + member.name)
    try:
        archive.extractall(target, filter="data")
    except TypeError:
        archive.extractall(target)


def restore(args: argparse.Namespace) -> int:
    require_root()
    if args.confirm != RESTORE_CONFIRM:
        fail("Restore requires --confirm " + RESTORE_CONFIRM, code=2)
    layout = Layout.load(Path(args.base_dir), args.host_id)
    paths = layout.tenant(args.tenant)
    metadata = paths.require()

    if getattr(args, "from_s3", None):
        config = offsite_config(layout)
        if not config:
            fail("No S3-compatible storage configured. Run: sudo vlux-cloud r2 configure ...")
        ensure_dir(paths.backups, 0o700)
        archive = paths.backups / Path(args.from_s3).name
        info("Downloading " + args.from_s3 + " from bucket " + str(config["bucket"]))
        written = s3_download(config, args.from_s3, archive)
        os.chmod(archive, 0o600)
        info("Downloaded " + str(written) + " bytes")
    elif args.backup:
        archive = Path(args.backup)
        if not archive.is_absolute():
            candidate = paths.backups / archive.name
            archive = candidate if candidate.exists() else archive.resolve()
    else:
        fail("Provide a local backup path or --from-s3 <object key>", code=2)
    if not archive.is_file():
        fail("Backup archive not found: " + str(archive))

    workdir = paths.backups / ("_restore-" + utc_stamp())
    ensure_dir(workdir, 0o700)
    try:
        with tarfile.open(archive, "r:gz") as handle:
            safe_extract(handle, workdir)
        roots = [path for path in workdir.iterdir() if path.is_dir()]
        if len(roots) != 1:
            fail("Backup archive must contain exactly one root directory")
        source = roots[0]

        manifest = read_json(source / "manifest.json")
        if manifest.get("backup_format") != "vlux-cloud/1":
            fail("Unrecognised backup format: " + str(manifest.get("backup_format")))
        if manifest.get("tenant") != paths.name:
            fail(
                "Backup belongs to tenant "
                + str(manifest.get("tenant"))
                + " and cannot be restored into "
                + paths.name
            )
        for name, expected in manifest.get("files", {}).items():
            path = source / name
            if not path.exists():
                fail("Backup is missing " + name)
            actual = sha256_file(path)
            if actual != expected["sha256"]:
                fail("Checksum mismatch for " + name + " in backup " + manifest["backup_id"])
        info("Backup checksums verified for " + manifest["backup_id"])

        if not args.no_safety_backup:
            info("Taking a pre-restore safety backup of the current state")
            safety = create_backup(
                layout, paths, label="pre-restore", offsite=False, timeout=args.timeout
            )
            info("Pre-restore safety backup: " + safety["backup_id"])
            metadata = paths.require()

        db_name = metadata["database"]["name"]
        db_role = metadata["database"]["role"]
        pg_container = paths.pg_container

        info("Stopping the app before replacing database and filestore")
        stop_app(paths)

        run(
            [
                "docker", "exec", "-i", pg_container,
                "psql", "-v", "ON_ERROR_STOP=1", "-U", db_role, "-d", "postgres", "-c",
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '"
                + db_name
                + "' AND pid <> pg_backend_pid()",
            ],
            capture=True,
        )
        run(
            [
                "docker", "exec", "-i", pg_container,
                "psql", "-v", "ON_ERROR_STOP=1", "-U", db_role, "-d", "postgres", "-c",
                'DROP DATABASE IF EXISTS "' + db_name + '"',
            ],
            capture=True,
        )
        run(
            [
                "docker", "exec", "-i", pg_container,
                "psql", "-v", "ON_ERROR_STOP=1", "-U", db_role, "-d", "postgres", "-c",
                'CREATE DATABASE "' + db_name + '" OWNER "' + db_role
                + "\" TEMPLATE template0 ENCODING 'UTF8'",
            ],
            capture=True,
        )
        with (source / "database.dump").open("rb") as stream:
            result = subprocess.run(
                [
                    "docker", "exec", "-i", pg_container,
                    "pg_restore", "--no-owner", "--no-privileges", "--exit-on-error",
                    "-U", db_role, "-d", db_name,
                ],
                stdin=stream,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=7200,
            )
        if result.returncode != 0:
            fail(
                "pg_restore failed: "
                + (result.stderr or b"").decode("utf-8", "replace").strip()[:800]
            )

        info("Replacing the tenant filestore")
        if paths.filestore.exists():
            shutil.rmtree(paths.filestore)
        with tarfile.open(source / "filestore.tar.gz", "r:gz") as handle:
            safe_extract(handle, paths.root)
        ensure_dir(paths.filestore, 0o750)
        chown_tree(paths.filestore, APP_UID, APP_GID)

        start_app(paths, args.timeout)
        report = tenant_health_report(layout, paths)
        if report["status"] != "ok":
            fail("Restore finished but health is degraded: " + json.dumps(report, sort_keys=True), code=3)

        payload = {
            "restore": "PASS",
            "tenant": paths.name,
            "backup_id": manifest["backup_id"],
            "archive": str(archive),
            "checksums_verified": "PASS",
            "safety_backup": "SKIPPED" if args.no_safety_backup else safety["backup_id"],
            "health": report,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# upgrade, disable
# --------------------------------------------------------------------------


def smoke_endpoints(layout: Layout, paths: TenantPaths, metadata: dict) -> dict:
    domain = metadata["domain"]
    db_name = metadata["database"]["name"]
    if layout.edge_mode() == "cloudflare_tunnel":
        code, body = edge_probe(layout, domain, db_name)
        ok = code == 0 and '"status": "ok"' in body
        return {"health": "PASS" if ok else "FAIL", "status": "ok" if ok else "degraded"}
    context = build_ssl_context(layout, metadata.get("tls_mode", "public"))
    results = {}
    probes = {
        "health": ("/vlux/health?db=" + db_name, '"status": "ok"'),
        "web_login": ("/web/login?db=" + db_name, "login"),
        "scanner": ("/vlux/scanner?db=" + db_name, None),
        "owner_manifest": ("/vlux-owner/manifest.webmanifest?db=" + db_name, "VLUX"),
    }
    for name, (path, needle) in probes.items():
        status, body = http_probe("https://" + domain + path, context=context, timeout=20)
        ok = status == 200 and (needle is None or needle.lower() in body.lower())
        results[name] = "PASS" if ok else "FAIL(" + str(status) + ")"
    results["status"] = "ok" if all(value == "PASS" for value in results.values()) else "degraded"
    return results


def upgrade(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    paths = layout.tenant(args.tenant)
    metadata = paths.require()

    image = args.image
    if "@sha256:" not in image:
        if not args.allow_unpinned:
            fail(
                "Cloud upgrades must reference an immutable digest "
                "(repo@sha256:...). Re-run with --allow-unpinned only on a lab host."
            )
        info("WARNING: upgrading to an unpinned image reference.")

    previous = {
        "image": dict(metadata.get("image", {})),
        "odoo": dict(metadata.get("odoo", {})),
        "cloud_version": metadata.get("cloud_version"),
        "recorded_at": utc_iso(),
    }
    write_json(paths.root / "previous-release.json", previous, mode=0o640)

    info("Taking the mandatory pre-upgrade backup")
    pre_backup = create_backup(
        layout, paths, label="pre-upgrade", offsite=args.offsite, timeout=args.timeout
    )
    metadata = paths.require()

    present = docker(["image", "inspect", image], check=False, capture=True).returncode == 0
    if args.pull or not present:
        docker(["pull", image], timeout=3600)
    else:
        info("Image already present locally; skipping pull (use --pull to force).")
    new_digest = image_digest(image)
    if new_digest == "UNKNOWN" and "@sha256:" in image:
        new_digest = image.split("@", 1)[1]

    tenant_env_file(
        paths,
        metadata["database"]["name"],
        metadata["database"]["role"],
        image,
        metadata.get("image", {}).get("postgres", POSTGRES_IMAGE),
    )

    failure_note = (
        "Upgrade failed after the image was swapped. The schema may already have "
        "been migrated, so a database rollback is NOT automatic. Restore with: "
        "sudo vlux-cloud restore " + paths.name + " " + pre_backup["archive"]
        + " --confirm " + RESTORE_CONFIRM
        + " and pin the previous image "
        + str(previous["image"].get("reference"))
        + " (digest " + str(previous["image"].get("digest")) + ")."
    )

    try:
        stop_app(paths)
        upgrade_addons(paths, metadata["database"]["name"])
        compose(paths.root, ["up", "-d", "app"])
        wait_container_healthy(paths.app_container, timeout=args.timeout)
        report = tenant_health_report(layout, paths)
        if report["status"] != "ok":
            fail("Post-upgrade health is degraded: " + json.dumps(report, sort_keys=True))
        smoke = smoke_endpoints(layout, paths, metadata)
        if smoke["status"] != "ok":
            fail("Post-upgrade smoke failed: " + json.dumps(smoke, sort_keys=True))
    except SystemExit as exc:
        detail = getattr(exc, "message", str(exc))
        print(
            json.dumps(
                {
                    "upgrade": "FAIL",
                    "tenant": paths.name,
                    "error": detail,
                    "previous_image": previous["image"].get("reference"),
                    "previous_image_digest": previous["image"].get("digest"),
                    "pre_upgrade_backup_id": pre_backup["backup_id"],
                    "pre_upgrade_backup": pre_backup["archive"],
                    "rollback": failure_note,
                    "data_destroyed": False,
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4

    metadata["image"]["reference"] = image
    metadata["image"]["digest"] = new_digest
    metadata["updated_at"] = utc_iso()
    metadata.setdefault("upgrade_history", []).append(
        {
            "at": utc_iso(),
            "from_image": previous["image"].get("reference"),
            "from_digest": previous["image"].get("digest"),
            "to_image": image,
            "to_digest": new_digest,
            "modules": list(PRODUCTIVE_ADDONS),
            "pre_upgrade_backup_id": pre_backup["backup_id"],
            "result": "PASS",
        }
    )
    write_json(paths.metadata, metadata, mode=0o640)

    print(
        json.dumps(
            {
                "upgrade": "PASS",
                "tenant": paths.name,
                "previous_image": previous["image"].get("reference"),
                "previous_image_digest": previous["image"].get("digest"),
                "image": image,
                "image_digest": new_digest,
                "modules_upgraded": list(PRODUCTIVE_ADDONS),
                "pre_upgrade_backup_id": pre_backup["backup_id"],
                "health": report,
                "smoke": smoke,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def disable(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    paths = layout.tenant(args.tenant)
    metadata = paths.require()

    removed_route = False
    if paths.caddy_route.exists():
        paths.caddy_route.unlink()
        edge_reload(layout)
        removed_route = True

    compose(paths.root, ["stop", "app"], timeout=300)
    stopped = ["app"]
    if args.stop_database:
        compose(paths.root, ["stop", "postgres"], timeout=300)
        stopped.append("postgres")

    metadata["state"] = "DISABLED"
    metadata["disabled_at"] = utc_iso()
    write_json(paths.metadata, metadata, mode=0o640)

    print(
        json.dumps(
            {
                "disable": "PASS",
                "tenant": paths.name,
                "edge_route_removed": removed_route,
                "containers_stopped": stopped,
                "preserved": {
                    "postgres_data": str(paths.postgres),
                    "filestore": str(paths.filestore),
                    "backups": str(paths.backups),
                    "secrets": str(paths.secrets),
                },
                "destructive_delete": "NOT_PERFORMED",
                "note": (
                    "Disable never deletes data. Re-enable with `vlux-cloud provision "
                    + paths.name
                    + " --domain "
                    + metadata["domain"]
                    + " --owner-email "
                    + metadata.get("owner_email", "<owner>")
                    + "`."
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0




# --------------------------------------------------------------------------
# Cloudflare Tunnel staging
# --------------------------------------------------------------------------


def cloudflared_account(image: str) -> tuple[int, int]:
    """Resolve the uid/gid cloudflared runs as.

    The image is distroless and has no shell, so the account is read from the
    image config rather than by running `id` inside it.
    """
    result = docker(
        ["image", "inspect", "--format", "{{.Config.User}}", image], check=False, capture=True
    )
    raw = (result.stdout or "").strip()
    if result.returncode == 0 and raw:
        parts = raw.split(":")
        if parts[0].isdigit():
            uid = int(parts[0])
            gid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else uid
            return uid, gid
    return CLOUDFLARED_UID, CLOUDFLARED_GID


def tunnel_compose_dir(layout: Layout) -> Path:
    return layout.tunnel


def render_tunnel_stack(layout: Layout, image: str) -> None:
    ensure_dir(layout.tunnel, 0o750)
    write_file(
        layout.tunnel / "compose.yaml",
        render(
            TEMPLATE_DIR / "cloudflared-compose.yaml.tmpl",
            {
                "__PROJECT__": layout.container_prefix + "tunnel",
                "__TUNNEL_CONTAINER__": layout.tunnel_container,
                "__EDGE_NETWORK__": layout.edge_network,
                "__METRICS_PORT__": str(CLOUDFLARED_METRICS_PORT),
                "__TOKEN_FILE__": str(layout.tunnel_token_file),
            },
        ),
        0o640,
    )
    write_file(
        layout.tunnel / ".env",
        "\n".join(
            [
                "# Rendered by vlux-cloud staging-init. Holds no secret:",
                "# the tunnel token lives in " + str(layout.tunnel_token_file) + " (0600).",
                "VLUX_CLOUDFLARED_IMAGE=" + image,
                "VLUX_TUNNEL_LOGLEVEL=info",
                "",
            ]
        ),
        0o640,
    )


def install_tunnel_token(layout: Layout, source: Path, image: str) -> dict:
    """Copy the operator's token file into the tenant-independent secret store.

    The token is never echoed. Only its length and a short fingerprint are kept
    so operators can tell two tokens apart without revealing either.
    """
    if not source.is_file():
        fail("Tunnel token file not found: " + str(source))
    token = source.read_text(encoding="utf-8").strip()
    if not token:
        fail("Tunnel token file is empty: " + str(source))
    if len(token) < 40:
        fail("Tunnel token looks too short to be a Cloudflare tunnel token.")
    if token.startswith("http://") or token.startswith("https://"):
        fail(
            "That looks like a connector URL, not a tunnel token. Copy the token "
            "value from the Cloudflare dashboard install command."
        )

    ensure_dir(layout.secrets, 0o700)
    uid, gid = cloudflared_account(image)
    # cloudflared runs unprivileged, so the mounted token must be readable by
    # its account. 0600 still keeps every other host user out.
    write_file(layout.tunnel_token_file, token, 0o600, uid=uid, gid=gid)
    return {
        "stored_at": str(layout.tunnel_token_file),
        "mode": "0600",
        "owner_uid": uid,
        "length": len(token),
        "fingerprint": hashlib.sha256(token.encode("utf-8")).hexdigest()[:12],
    }


def tunnel_probe(layout: Layout, path: str = "/ready", timeout: int = 10) -> tuple[int, str]:
    """Probe the cloudflared metrics endpoint from inside the edge network.

    Nothing is published on the host, so the probe runs in a throwaway
    container attached to the same private network.
    """
    url = (
        "http://" + layout.tunnel_container + ":" + str(CLOUDFLARED_METRICS_PORT) + path
    )
    image = read_json(layout.host_state).get("probe_image") or DEFAULT_APP_IMAGE
    result = docker(
        [
            "run", "--rm", "--network", layout.edge_network,
            "--entrypoint", "curl", image,
            "-fsS", "--max-time", str(timeout), url,
        ],
        check=False,
        capture=True,
    )
    return result.returncode, (result.stdout or "").strip()


def tunnel_state(layout: Layout) -> dict:
    status, health, restarts, exit_code = container_runtime_state(layout.tunnel_container)
    report = {
        "container": layout.tunnel_container,
        "status": status,
        "restarts": restarts,
        "exit_code": exit_code,
        "networks": [],
        "ready": "UNKNOWN",
        "connections": 0,
    }
    if status == "absent":
        report["tunnel"] = "NOT_INSTALLED"
        return report
    nets = docker(
        [
            "inspect", "-f",
            "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}",
            layout.tunnel_container,
        ],
        check=False,
        capture=True,
    )
    report["networks"] = sorted((nets.stdout or "").split())
    if status != "running":
        report["tunnel"] = "STOPPED"
        return report
    code, body = tunnel_probe(layout, "/ready")
    if code == 0:
        report["ready"] = "PASS"
        try:
            payload = json.loads(body)
            report["connections"] = int(payload.get("readyConnections", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            report["connections"] = 0
        report["tunnel"] = "CONNECTED" if report["connections"] > 0 else "NO_CONNECTIONS"
    else:
        report["ready"] = "FAIL"
        report["tunnel"] = "UNREACHABLE"
    return report


def staging_init(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    require_host_init(layout)

    host_state = read_json(layout.host_state)
    edge_mode = host_state.get("edge_mode", DEFAULT_EDGE_MODE)
    if edge_mode != "cloudflare_tunnel":
        fail(
            "This host runs edge mode " + edge_mode + ". Re-run host-init with "
            "--edge-mode cloudflare_tunnel before enabling the staging tunnel."
        )

    published = edge_published_ports(layout)
    if published:
        fail(
            "The edge is publishing host ports (" + ", ".join(published)
            + "). Tunnel staging must expose nothing on the host."
        )

    image = args.cloudflared_image or CLOUDFLARED_IMAGE
    if "@sha256:" not in image and not args.allow_unpinned:
        fail("The cloudflared image must be pinned by digest (use --allow-unpinned on a lab host).")

    token_info = install_tunnel_token(layout, Path(args.tunnel_token_file), image)
    render_tunnel_stack(layout, image)

    if args.pull:
        docker(["pull", image])
    compose(layout.tunnel, ["up", "-d", "--remove-orphans"])

    def ready():
        state = tunnel_state(layout)
        return state.get("tunnel") == "CONNECTED", "tunnel state: " + str(state.get("tunnel"))

    if not args.no_wait:
        wait_for(ready, timeout=args.timeout, interval=5.0, description="the Cloudflare tunnel to connect")

    state = tunnel_state(layout)
    host_state["tunnel"] = {
        "image": image,
        "digest": image_digest(image),
        "container": layout.tunnel_container,
        "token_file": token_info["stored_at"],
        "token_fingerprint": token_info["fingerprint"],
        "mode": "REMOTE_MANAGED",
        "configured_at": utc_iso(),
    }
    host_state["deployment_class"] = "CLOUDFLARE_TEMPORARY_STAGING"
    write_json(layout.host_state, host_state, mode=0o640)

    payload = {
        "staging_init": "PASS",
        "deployment_class": "CLOUDFLARE_TEMPORARY_STAGING",
        "edge_mode": edge_mode,
        "host_public_ports": "NONE",
        "cloudflared_image": image,
        "cloudflared_digest": host_state["tunnel"]["digest"],
        "tunnel_mode": "REMOTE_MANAGED",
        "token_storage": token_info["stored_at"] + " (0600, uid " + str(token_info["owner_uid"]) + ")",
        "token_fingerprint": token_info["fingerprint"],
        "tunnel": state,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if state.get("tunnel") == "CONNECTED" or args.no_wait else 2


def tunnel_command(args: argparse.Namespace) -> int:
    layout = Layout.load(Path(args.base_dir), args.host_id)
    if args.action in {"start", "stop"}:
        require_root()
        if not (layout.tunnel / "compose.yaml").exists():
            fail("The tunnel is not installed. Run: sudo vlux-cloud staging-init --tunnel-token-file <path>")
        compose(layout.tunnel, ["up", "-d"] if args.action == "start" else ["stop"], timeout=300)
    state = tunnel_state(layout)
    print(json.dumps(state, indent=2, sort_keys=True))
    if args.action == "stop":
        return 0
    return 0 if state.get("tunnel") == "CONNECTED" else 2


# --------------------------------------------------------------------------
# S3-compatible object storage: multipart, download, delete
# --------------------------------------------------------------------------

MULTIPART_THRESHOLD = 128 * 1024 * 1024
# R2 requires every part except the last to be the same size, so a fixed part
# size is used rather than an adaptive one.
MULTIPART_PART_SIZE = 64 * 1024 * 1024
MAX_MULTIPART_PARTS = 10000


def _xml_first(body: bytes, tag: str) -> str:
    match = re.search(
        r"<" + re.escape(tag) + r"[^>]*>(.*?)</" + re.escape(tag) + r">",
        body.decode("utf-8", "replace"),
        re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _s3_multipart_upload(config: dict, archive: Path, object_key: str) -> dict:
    size = archive.stat().st_size
    part_size = MULTIPART_PART_SIZE
    while size / part_size > MAX_MULTIPART_PARTS:
        part_size *= 2

    status, _headers, body = _s3_request(
        config, "POST", object_key, payload_sha256=EMPTY_SHA256, query={"uploads": ""}
    )
    if status != 200:
        fail("Could not start a multipart upload (HTTP " + str(status) + "): "
             + body.decode("utf-8", "replace")[:300])
    upload_id = _xml_first(body, "UploadId")
    if not upload_id:
        fail("The storage endpoint did not return an UploadId.")

    parts: list[tuple[int, str]] = []
    try:
        with archive.open("rb") as stream:
            number = 0
            while True:
                chunk = stream.read(part_size)
                if not chunk:
                    break
                number += 1
                part_status, part_headers, part_body = _s3_request(
                    config,
                    "PUT",
                    object_key,
                    payload_sha256=hashlib.sha256(chunk).hexdigest(),
                    body=chunk,
                    content_length=len(chunk),
                    query={"partNumber": str(number), "uploadId": upload_id},
                )
                if part_status != 200:
                    fail(
                        "Part " + str(number) + " failed (HTTP " + str(part_status) + "): "
                        + part_body.decode("utf-8", "replace")[:200]
                    )
                etag = str(part_headers.get("ETag") or part_headers.get("Etag") or "").strip()
                if not etag:
                    fail("Part " + str(number) + " returned no ETag.")
                parts.append((number, etag))
    except BaseException:
        _s3_request(
            config, "DELETE", object_key, payload_sha256=EMPTY_SHA256,
            query={"uploadId": upload_id},
        )
        raise

    document = "<CompleteMultipartUpload>" + "".join(
        "<Part><PartNumber>" + str(number) + "</PartNumber><ETag>" + etag + "</ETag></Part>"
        for number, etag in parts
    ) + "</CompleteMultipartUpload>"
    payload = document.encode("utf-8")
    status, _headers, body = _s3_request(
        config,
        "POST",
        object_key,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        body=payload,
        content_length=len(payload),
        query={"uploadId": upload_id},
    )
    text = body.decode("utf-8", "replace")
    if status != 200 or "<Error" in text:
        _s3_request(
            config, "DELETE", object_key, payload_sha256=EMPTY_SHA256,
            query={"uploadId": upload_id},
        )
        fail("Completing the multipart upload failed (HTTP " + str(status) + "): " + text[:300])
    return {"parts": len(parts), "part_size_bytes": part_size}


def s3_download(config: dict, object_key: str, destination: Path, timeout: int = 900) -> int:
    request, _headers = _s3_signed_request(
        config, "GET", object_key, payload_sha256=EMPTY_SHA256
    )
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                fail("Download failed with HTTP " + str(response.status))
            with destination.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
    except urllib.error.HTTPError as exc:
        fail(
            "Download of " + object_key + " failed with HTTP " + str(exc.code) + ": "
            + exc.read(400).decode("utf-8", "replace")
        )
    except OSError as exc:
        fail("Download of " + object_key + " failed: " + str(exc))
    return written


def s3_delete(config: dict, object_key: str) -> int:
    status, _headers, _body = _s3_request(
        config, "DELETE", object_key, payload_sha256=EMPTY_SHA256
    )
    return status


def s3_object_size(config: dict, object_key: str) -> int | None:
    status, headers, _body = _s3_request(
        config, "HEAD", object_key, payload_sha256=EMPTY_SHA256
    )
    if status != 200:
        return None
    try:
        return int(headers.get("Content-Length", ""))
    except (TypeError, ValueError):
        return None


def r2_configure(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    ensure_dir(layout.secrets, 0o700)

    access_key_file = layout.secrets / "r2_access_key_id"
    secret_key_file = layout.secrets / "r2_secret_access_key"
    for target, source in (
        (access_key_file, args.access_key_id_file),
        (secret_key_file, args.secret_access_key_file),
    ):
        if source:
            path = Path(source)
            if not path.is_file():
                fail("Credential file not found: " + str(path))
            value = path.read_text(encoding="utf-8").strip()
            if not value:
                fail("Credential file is empty: " + str(path))
            write_file(target, value, 0o600)
    for required in (access_key_file, secret_key_file):
        if not required.exists():
            fail(
                "Missing " + required.name + ". Provide --access-key-id-file and "
                "--secret-access-key-file, or write the value yourself with mode 0600."
            )

    endpoint = args.endpoint
    if args.account_id:
        endpoint = "https://" + args.account_id + ".r2.cloudflarestorage.com"
    if not endpoint:
        fail("Provide --account-id (Cloudflare R2) or an explicit --endpoint.")

    config = {
        "provider": args.provider,
        "endpoint": endpoint,
        "region": args.region,
        "bucket": args.bucket,
        "prefix": args.prefix,
        "access_key_id_file": str(access_key_file),
        "secret_access_key_file": str(secret_key_file),
        "configured_at": utc_iso(),
    }
    write_json(layout.offsite_config, config, mode=0o600)
    payload = dict(config)
    payload["credentials"] = "stored root-only, never echoed"
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def r2_test(args: argparse.Namespace) -> int:
    layout = Layout.load(Path(args.base_dir), args.host_id)
    config = offsite_config(layout)
    if not config:
        fail("No S3-compatible storage configured. Run: sudo vlux-cloud r2 configure ...")

    payload = os.urandom(4096)
    digest = hashlib.sha256(payload).hexdigest()
    key = "/".join(
        [str(config["prefix"]).strip("/"), "_healthcheck", "vlux-r2-test-" + utc_stamp() + ".bin"]
    )
    results = {
        "endpoint_host": urllib.parse.urlsplit(str(config["endpoint"])).netloc,
        "bucket": config["bucket"],
        "object_key": key,
        "note": "synthetic payload only, never customer data",
    }

    status, _headers, body = _s3_request(
        config, "PUT", key, payload_sha256=digest, body=payload, content_length=len(payload)
    )
    results["R2_WRITE"] = "PASS" if status in (200, 201) else "FAIL(" + str(status) + ")"
    if status not in (200, 201):
        results["error"] = body.decode("utf-8", "replace")[:300]
        print(json.dumps(results, indent=2, sort_keys=True))
        return 2

    status, _headers, fetched = _s3_request(config, "GET", key, payload_sha256=EMPTY_SHA256)
    results["R2_READ"] = "PASS" if status == 200 else "FAIL(" + str(status) + ")"
    results["R2_INTEGRITY"] = (
        "PASS" if status == 200 and hashlib.sha256(fetched).hexdigest() == digest else "FAIL"
    )

    delete_status = s3_delete(config, key)
    gone = s3_object_size(config, key) is None
    results["R2_DELETE"] = "PASS" if delete_status in (200, 204) and gone else "FAIL"

    ok = all(results.get(name) == "PASS" for name in ("R2_WRITE", "R2_READ", "R2_INTEGRITY", "R2_DELETE"))
    results["status"] = "ok" if ok else "degraded"
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if ok else 2


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------


def backup_archives(paths: TenantPaths) -> list[Path]:
    if not paths.backups.is_dir():
        return []
    return sorted(
        (item for item in paths.backups.iterdir() if item.name.endswith(".tar.gz")),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def apply_retention(
    paths: TenantPaths,
    *,
    keep_daily: int = 7,
    keep_weekly: int = 4,
    keep_monthly: int = 3,
    dry_run: bool = False,
) -> dict:
    """Keep the newest backup per day, ISO week and month within the windows.

    Retention only ever runs after a new backup has been verified, and the most
    recent archive is never a candidate for deletion.
    """
    archives = backup_archives(paths)
    if len(archives) <= 1:
        return {"kept": [item.name for item in archives], "deleted": [], "reason": "nothing to prune"}

    keep: set[Path] = {archives[0]}
    seen_days: dict[str, Path] = {}
    seen_weeks: dict[str, Path] = {}
    seen_months: dict[str, Path] = {}
    for archive in archives:
        stamp = _dt.datetime.fromtimestamp(archive.stat().st_mtime, _dt.timezone.utc)
        day = stamp.strftime("%Y-%m-%d")
        iso = stamp.isocalendar()
        week = str(iso[0]) + "-W" + str(iso[1])
        month = stamp.strftime("%Y-%m")
        if day not in seen_days and len(seen_days) < keep_daily:
            seen_days[day] = archive
            keep.add(archive)
        if week not in seen_weeks and len(seen_weeks) < keep_weekly:
            seen_weeks[week] = archive
            keep.add(archive)
        if month not in seen_months and len(seen_months) < keep_monthly:
            seen_months[month] = archive
            keep.add(archive)

    deleted = []
    for archive in archives:
        if archive in keep:
            continue
        deleted.append(archive.name)
        if not dry_run:
            archive.unlink(missing_ok=True)
            Path(str(archive) + ".sha256").unlink(missing_ok=True)
    return {
        "kept": sorted(item.name for item in keep),
        "deleted": deleted,
        "policy": {"daily": keep_daily, "weekly": keep_weekly, "monthly": keep_monthly},
        "dry_run": dry_run,
    }


# --------------------------------------------------------------------------
# Maintenance / write freeze
# --------------------------------------------------------------------------


def set_maintenance(layout: Layout, paths: TenantPaths, enabled: bool, timeout: int) -> dict:
    metadata = paths.require()
    edge_mode = layout.edge_mode()
    domain = metadata["domain"]
    tls_mode = metadata.get("tls_mode", "public")

    if enabled:
        write_edge_route(
            layout, paths,
            tenant_route_content(paths, domain, tls_mode, edge_mode, maintenance=True),
        )
        # Stopping the app also stops its cron threads, so nothing can write to
        # the database behind the 503 while a final export runs.
        stop_app(paths)
        metadata["maintenance"] = {"enabled": True, "since": utc_iso()}
    else:
        start_app(paths, timeout)
        write_edge_route(
            layout, paths, tenant_route_content(paths, domain, tls_mode, edge_mode)
        )
        metadata["maintenance"] = {"enabled": False, "since": utc_iso()}
    write_json(paths.metadata, metadata, mode=0o640)
    return metadata["maintenance"]


def maintenance_enabled(paths: TenantPaths) -> bool:
    return bool(read_json(paths.metadata).get("maintenance", {}).get("enabled"))


def maintenance_command(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    paths = layout.tenant(args.tenant)
    paths.require()
    state = set_maintenance(layout, paths, args.action == "enable", args.timeout)
    print(
        json.dumps(
            {
                "maintenance": "ENABLED" if state["enabled"] else "DISABLED",
                "tenant": paths.name,
                "write_freeze": "ENFORCED" if state["enabled"] else "RELEASED",
                "edge_response": "503" if state["enabled"] else "proxy",
                "app_container": container_health(paths.app_container),
                "data_destroyed": False,
                "since": state["since"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


# --------------------------------------------------------------------------
# Migration: export, precheck, import, verify
# --------------------------------------------------------------------------

# Only stored tables are compared. Values Odoo recomputes (qty_available and
# friends) are derived at read time, so they are deliberately not compared;
# what must survive a migration is the stored data the dump carries.
METRIC_QUERIES = (
    ("partners", "SELECT count(*) FROM res_partner"),
    ("users", "SELECT count(*) FROM res_users"),
    ("companies", "SELECT count(*) FROM res_company"),
    ("product_templates", "SELECT count(*) FROM product_template"),
    ("product_variants", "SELECT count(*) FROM product_product"),
    ("pos_orders", "SELECT count(*) FROM pos_order"),
    ("pos_order_lines", "SELECT count(*) FROM pos_order_line"),
    ("pos_sessions", "SELECT count(*) FROM pos_session"),
    ("pos_payments", "SELECT count(*) FROM pos_payment"),
    ("stock_quants", "SELECT count(*) FROM stock_quant"),
    ("stock_quant_quantity_sum", "SELECT coalesce(sum(quantity), 0)::numeric(20,4) FROM stock_quant"),
    ("stock_moves", "SELECT count(*) FROM stock_move"),
    ("stock_move_lines", "SELECT count(*) FROM stock_move_line"),
    ("stock_pickings", "SELECT count(*) FROM stock_picking"),
    ("account_moves", "SELECT count(*) FROM account_move"),
    ("attachments", "SELECT count(*) FROM ir_attachment"),
    ("installed_modules", "SELECT count(*) FROM ir_module_module WHERE state = 'installed'"),
)

INVENTORY_METRICS = (
    "stock_quants",
    "stock_quant_quantity_sum",
    "stock_moves",
    "stock_move_lines",
    "stock_pickings",
)
POS_METRICS = ("pos_orders", "pos_order_lines", "pos_sessions", "pos_payments")


def filestore_metrics(paths: TenantPaths, db_name: str) -> dict:
    """Count the Odoo filestore only, excluding volatile session files."""
    root = paths.filestore / "filestore" / db_name
    files = 0
    total = 0
    if root.is_dir():
        for current, _dirs, names in os.walk(root, onerror=lambda _e: None):
            for name in names:
                try:
                    total += os.lstat(os.path.join(current, name)).st_size
                except OSError:
                    continue
                files += 1
    return {"files": files, "bytes": total, "path": str(root)}


def tenant_metrics(paths: TenantPaths) -> dict:
    metadata = paths.require()
    db_name = metadata["database"]["name"]
    values: dict = {}
    for name, query in METRIC_QUERIES:
        result = psql(paths, query, check=False)
        raw = (result.stdout or "").strip() if result.returncode == 0 else ""
        if result.returncode != 0 or raw == "":
            values[name] = None
            continue
        values[name] = raw
    values["filestore"] = filestore_metrics(paths, db_name)
    return {
        "collected_at": utc_iso(),
        "database": db_name,
        "values": values,
        "note": (
            "Stored tables only. Odoo recomputes derived stock figures such as "
            "qty_available at read time, so they are not compared directly."
        ),
    }


def migration_export(args: argparse.Namespace) -> int:
    require_root()
    layout = Layout.load(Path(args.base_dir), args.host_id)
    paths = layout.tenant(args.tenant)
    metadata = paths.require()

    frozen = maintenance_enabled(paths)
    if not frozen and not args.allow_live:
        fail(
            "A cutover export must run with writes frozen, otherwise a sale made "
            "after the dump would be lost. Run `vlux-cloud maintenance enable "
            + paths.name + "` first, or pass --allow-live for a rehearsal export.",
            code=2,
        )
    write_freeze = "ENFORCED" if frozen else "NOT_ENFORCED_REHEARSAL"

    metrics = tenant_metrics(paths)
    record = create_backup(
        layout,
        paths,
        label="migration",
        offsite=args.offsite,
        timeout=args.timeout,
        tier="migration",
        migration=True,
        extra_manifest={
            "migration": {
                "write_freeze": write_freeze,
                "cutover_ready": frozen,
                "source_host_id": layout.host_id or "default",
                "source_cloud_version": CLOUD_VERSION,
                "source_edge_mode": layout.edge_mode(),
                "source_domain": metadata["domain"],
                "addons": list(PRODUCTIVE_ADDONS),
            },
            "metrics": metrics,
        },
    )
    record["write_freeze"] = write_freeze
    record["migration_export"] = "PASS"
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def migration_precheck_report(
    layout: Layout, paths: TenantPaths, manifest: dict, archive_size: int
) -> dict:
    checks: dict = {}
    source_image = manifest.get("image", {})
    checks["source_cloud_version"] = manifest.get("cloud_version")
    checks["target_cloud_version"] = CLOUD_VERSION
    checks["source_odoo_commit"] = source_image.get("odoo_commit")
    checks["target_odoo_commit"] = ODOO_COMMIT
    checks["source_db_major"] = manifest.get("database", {}).get("major")
    checks["target_db_major"] = CLOUD_DB_MAJOR
    checks["source_addons"] = manifest.get("migration", {}).get("addons")
    checks["target_addons"] = list(PRODUCTIVE_ADDONS)

    critical: list[str] = []
    if checks["source_odoo_commit"] and checks["source_odoo_commit"] != ODOO_COMMIT:
        critical.append(
            "Odoo commit mismatch: backup was taken on "
            + str(checks["source_odoo_commit"])
            + " but this host runs " + ODOO_COMMIT
        )
    if checks["source_db_major"] and str(checks["source_db_major"]) != CLOUD_DB_MAJOR:
        critical.append(
            "PostgreSQL major mismatch: backup is "
            + str(checks["source_db_major"]) + ", this host is " + CLOUD_DB_MAJOR
        )
    source_addons = checks["source_addons"] or []
    missing_addons = [name for name in source_addons if name not in PRODUCTIVE_ADDONS]
    if missing_addons:
        critical.append("Target does not ship required addons: " + ", ".join(missing_addons))

    usage = shutil.disk_usage(str(layout.base))
    needed = archive_size * 4
    checks["disk_free_bytes"] = usage.free
    checks["disk_needed_bytes"] = needed
    if usage.free < needed:
        critical.append(
            "Insufficient disk: " + str(usage.free) + " bytes free, roughly "
            + str(needed) + " needed to unpack and restore."
        )

    app_state = container_state(paths.app_container).get("Status")
    checks["target_tenant_app_state"] = app_state
    checks["target_tenant_exists"] = paths.exists()
    if app_state == "running":
        critical.append(
            "Tenant " + paths.name + " already has a running app on this host. "
            "Importing over a live tenant is refused."
        )

    checks["critical"] = critical
    checks["status"] = "PASS" if not critical else "BLOCKED"
    return checks


def _open_migration_archive(
    layout: Layout, paths: TenantPaths, args: argparse.Namespace, workdir: Path
) -> tuple[Path, Path, dict]:
    """Resolve, download if needed, extract and verify a migration archive."""
    if args.from_s3:
        config = offsite_config(layout)
        if not config:
            fail("No S3-compatible storage configured. Run: sudo vlux-cloud r2 configure ...")
        ensure_dir(paths.backups, 0o700)
        archive = paths.backups / Path(args.from_s3).name
        info("Downloading " + args.from_s3 + " from " + str(config["bucket"]))
        written = s3_download(config, args.from_s3, archive)
        info("Downloaded " + str(written) + " bytes")
        os.chmod(archive, 0o600)
    else:
        archive = Path(args.backup)
        if not archive.is_absolute():
            candidate = paths.backups / archive.name
            archive = candidate if candidate.exists() else archive.resolve()
    if not archive.is_file():
        fail("Migration archive not found: " + str(archive))

    with tarfile.open(archive, "r:gz") as handle:
        safe_extract(handle, workdir)
    roots = [item for item in workdir.iterdir() if item.is_dir()]
    if len(roots) != 1:
        fail("Migration archive must contain exactly one root directory")
    source = roots[0]

    manifest = read_json(source / "manifest.json")
    if manifest.get("backup_format") != "vlux-cloud/1":
        fail("Unrecognised backup format: " + str(manifest.get("backup_format")))
    for name, expected in manifest.get("files", {}).items():
        item = source / name
        if not item.exists():
            fail("Migration archive is missing " + name)
        if sha256_file(item) != expected["sha256"]:
            fail("Checksum mismatch for " + name + " in " + str(manifest.get("backup_id")))
    info("Migration archive checksums verified: " + str(manifest.get("backup_id")))
    return archive, source, manifest


def migration_precheck(args: argparse.Namespace) -> int:
    layout = Layout.load(Path(args.base_dir), args.host_id)
    paths = layout.tenant(args.tenant)
    workdir = Path(args.workdir or "/tmp") / ("vlux-precheck-" + utc_stamp())
    ensure_dir(workdir, 0o700)
    try:
        archive, _source, manifest = _open_migration_archive(layout, paths, args, workdir)
        report = migration_precheck_report(layout, paths, manifest, archive.stat().st_size)
        report["backup_id"] = manifest.get("backup_id")
        report["source_tenant"] = manifest.get("tenant")
        report["write_freeze"] = manifest.get("migration", {}).get("write_freeze")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def migration_import(args: argparse.Namespace) -> int:
    require_root()
    if args.confirm != MIGRATION_IMPORT_CONFIRM:
        fail("Import requires --confirm " + MIGRATION_IMPORT_CONFIRM, code=2)
    layout = Layout.load(Path(args.base_dir), args.host_id)
    require_host_init(layout)
    paths = layout.tenant(args.tenant)

    workdir = paths.root / ("_import-" + utc_stamp()) if paths.root.exists() else Path(
        args.workdir or "/tmp"
    ) / ("vlux-import-" + utc_stamp())
    ensure_dir(workdir, 0o700)
    try:
        archive, source, manifest = _open_migration_archive(layout, paths, args, workdir)
        report = migration_precheck_report(layout, paths, manifest, archive.stat().st_size)
        if report["status"] != "PASS" and not args.allow_incompatible:
            print(json.dumps(report, indent=2, sort_keys=True), file=sys.stderr)
            fail("Migration precheck blocked the import. Nothing was changed.", code=3)

        source_metadata = read_json(source / "tenant.json")
        domain = validate_domain(args.domain or source_metadata.get("domain") or "")
        owner_email = source_metadata.get("owner_email") or "owner@" + domain

        pg_uid, pg_gid = postgres_account(args.postgres_image or POSTGRES_IMAGE)
        ensure_dir(paths.root, 0o750)
        ensure_dir(paths.secrets, 0o700)
        ensure_dir(paths.backups, 0o700)
        ensure_dir(paths.postgres, 0o700, uid=pg_uid, gid=pg_gid)
        ensure_dir(paths.config, 0o750, uid=APP_UID, gid=APP_GID)
        ensure_dir(paths.filestore, 0o750, uid=APP_UID, gid=APP_GID)
        ensure_dir(paths.logs, 0o750, uid=APP_UID, gid=APP_GID)

        # Infrastructure secrets are ALWAYS minted fresh on the destination.
        # Nothing secret travels inside a migration bundle.
        generated = []
        for name, length in (("db_password", 32), ("admin_passwd", 36)):
            if not paths.has_secret(name):
                paths.write_secret(name, secrets.token_urlsafe(length))
                generated.append(name)

        db_name = db_identifier(paths.name)
        db_user = db_identifier(paths.name)
        app_image = args.image or DEFAULT_APP_IMAGE
        postgres_image = args.postgres_image or POSTGRES_IMAGE
        workers = args.workers if args.workers is not None else DEFAULT_WORKERS
        max_cron = (
            args.max_cron_threads if args.max_cron_threads is not None
            else DEFAULT_MAX_CRON_THREADS
        )

        tenant_env_file(paths, db_name, db_user, app_image, postgres_image)
        render_tenant_files(
            paths,
            db_name=db_name,
            db_user=db_user,
            db_host=args.db_host or "postgres",
            workers=workers,
            max_cron_threads=max_cron,
            memory_soft_mb=args.limit_memory_soft_mb,
            memory_hard_mb=args.limit_memory_hard_mb,
            readonly_rootfs=not args.no_readonly_rootfs,
        )

        info("Starting isolated PostgreSQL for the imported tenant")
        compose(paths.root, ["up", "-d", "postgres"])
        wait_container_healthy(paths.pg_container, timeout=args.timeout)

        run(
            [
                "docker", "exec", "-i", paths.pg_container,
                "psql", "-v", "ON_ERROR_STOP=1", "-U", db_user, "-d", "postgres", "-c",
                'DROP DATABASE IF EXISTS "' + db_name + '"',
            ],
            capture=True,
        )
        run(
            [
                "docker", "exec", "-i", paths.pg_container,
                "psql", "-v", "ON_ERROR_STOP=1", "-U", db_user, "-d", "postgres", "-c",
                'CREATE DATABASE "' + db_name + '" OWNER "' + db_user
                + "\" TEMPLATE template0 ENCODING 'UTF8'",
            ],
            capture=True,
        )
        info("Restoring the database")
        with (source / "database.dump").open("rb") as stream:
            result = subprocess.run(
                [
                    "docker", "exec", "-i", paths.pg_container,
                    "pg_restore", "--no-owner", "--no-privileges", "--exit-on-error",
                    "-U", db_user, "-d", db_name,
                ],
                stdin=stream,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=7200,
            )
        if result.returncode != 0:
            fail(
                "pg_restore failed: "
                + (result.stderr or b"").decode("utf-8", "replace").strip()[:800]
            )

        info("Restoring the filestore")
        if paths.filestore.exists():
            shutil.rmtree(paths.filestore)
        with tarfile.open(source / "filestore.tar.gz", "r:gz") as handle:
            safe_extract(handle, paths.root)
        ensure_dir(paths.filestore, 0o750)
        chown_tree(paths.filestore, APP_UID, APP_GID)

        metadata = {
            "tenant": paths.name,
            "domain": domain,
            "edition": "cloud_managed",
            "cloud_version": CLOUD_VERSION,
            "tls_mode": args.tls_mode,
            "owner_email": owner_email,
            "data_class": source_metadata.get("data_class", DEFAULT_DATA_CLASS),
            "created_at": source_metadata.get("created_at") or utc_iso(),
            "updated_at": utc_iso(),
            "imported_at": utc_iso(),
            "imported_from": {
                "backup_id": manifest.get("backup_id"),
                "source_tenant": manifest.get("tenant"),
                "source_host_id": manifest.get("migration", {}).get("source_host_id"),
                "write_freeze": manifest.get("migration", {}).get("write_freeze"),
            },
            "database": {
                "engine": CLOUD_DB_ENGINE,
                "major": CLOUD_DB_MAJOR,
                "target": CLOUD_DB_TARGET,
                "name": db_name,
                "role": db_user,
                "host": args.db_host or "postgres",
                "port": DB_PORT,
                "public_exposure": "NONE",
            },
            "image": {
                "reference": app_image,
                "digest": image_digest(app_image),
                "postgres": postgres_image,
                "postgres_amd64_digest": POSTGRES_AMD64_DIGEST,
                "odoo_commit": ODOO_COMMIT,
                "python_version": PYTHON_VERSION,
            },
            "odoo": {
                "workers": workers,
                "max_cron_threads": max_cron,
                "http_port": HTTP_PORT,
                "gevent_port": GEVENT_PORT,
                "proxy_mode": True,
                "list_db": False,
                "addons": list(PRODUCTIVE_ADDONS),
            },
            "networks": {
                "private": paths.private_network,
                "edge": layout.edge_network,
                "app_alias": paths.app_alias,
            },
            "paths": {
                "root": str(paths.root),
                "filestore": str(paths.filestore),
                "logs": str(paths.logs),
                "backups": str(paths.backups),
                "secrets": str(paths.secrets),
            },
            "secrets_present": sorted(
                name for name in ("db_password", "admin_passwd") if paths.has_secret(name)
            ),
            "readonly_rootfs": not args.no_readonly_rootfs,
            "backup": {"last_success": None, "last_backup_id": None},
            "upgrade_history": [],
            "maintenance": {"enabled": False, "since": utc_iso()},
            "state": "IMPORTED_NO_TRAFFIC",
        }
        write_json(paths.metadata, metadata, mode=0o640)

        info("Starting Odoo (no edge route: the tenant receives no traffic yet)")
        compose(paths.root, ["up", "-d", "app"])
        wait_container_healthy(paths.app_container, timeout=args.timeout)

        app_probe = compose(
            paths.root,
            [
                "exec", "-T", "app", "curl", "-fsS",
                "http://127.0.0.1:" + str(HTTP_PORT) + "/vlux/health?db=" + db_name,
            ],
            check=False,
            capture=True,
        )
        app_health = (
            "PASS"
            if app_probe.returncode == 0 and '"status": "ok"' in (app_probe.stdout or "")
            else "FAIL"
        )

        live = tenant_metrics(paths)
        comparison = compare_metrics(manifest.get("metrics", {}), live)

        payload = {
            "migration_import": "PASS" if app_health == "PASS" else "DEGRADED",
            "tenant": paths.name,
            "domain": domain,
            "backup_id": manifest.get("backup_id"),
            "precheck": report,
            "database": db_name,
            "app_health": app_health,
            "secrets_minted_on_target": generated,
            "secrets_imported_from_bundle": [],
            "edge_route": "NOT_INSTALLED",
            "traffic": "DISABLED",
            "state": "IMPORTED_NO_TRAFFIC",
            "verification": comparison,
            "next_step": (
                "Review `vlux-cloud migration verify " + paths.name + " --backup <archive>`, "
                "then point Cloudflare at this host and run "
                "`vlux-cloud maintenance disable " + paths.name + "`."
            ),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if app_health == "PASS" and comparison["status"] == "MATCH" else 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def compare_metrics(source: dict, destination: dict) -> dict:
    """Compare the metrics captured at export against the live destination."""
    source_values = (source or {}).get("values") or {}
    dest_values = (destination or {}).get("values") or {}
    if not source_values:
        return {
            "status": "UNAVAILABLE",
            "reason": "the archive carries no source metrics",
            "differences": {},
        }

    differences = {}
    compared = []
    for name in sorted(set(source_values) | set(dest_values)):
        if name == "filestore":
            continue
        before = source_values.get(name)
        after = dest_values.get(name)
        if before is None and after is None:
            continue
        compared.append(name)
        if str(before) != str(after):
            differences[name] = {"source": before, "destination": after}

    fs_before = (source_values.get("filestore") or {})
    fs_after = (dest_values.get("filestore") or {})
    filestore = {
        "source_files": fs_before.get("files"),
        "destination_files": fs_after.get("files"),
        "source_bytes": fs_before.get("bytes"),
        "destination_bytes": fs_after.get("bytes"),
    }
    if fs_before.get("files") != fs_after.get("files") or fs_before.get("bytes") != fs_after.get("bytes"):
        differences["filestore"] = filestore

    return {
        "status": "MATCH" if not differences else "DIVERGENT",
        "compared_metrics": compared,
        "inventory": {
            name: {"source": source_values.get(name), "destination": dest_values.get(name)}
            for name in INVENTORY_METRICS
            if source_values.get(name) is not None or dest_values.get(name) is not None
        },
        "pos": {
            name: {"source": source_values.get(name), "destination": dest_values.get(name)}
            for name in POS_METRICS
            if source_values.get(name) is not None or dest_values.get(name) is not None
        },
        "filestore": filestore,
        "differences": differences,
        "limitation": (destination or {}).get("note", ""),
    }


def migration_verify(args: argparse.Namespace) -> int:
    layout = Layout.load(Path(args.base_dir), args.host_id)
    paths = layout.tenant(args.tenant)
    paths.require()
    workdir = Path(args.workdir or "/tmp") / ("vlux-verify-" + utc_stamp())
    ensure_dir(workdir, 0o700)
    try:
        _archive, _source, manifest = _open_migration_archive(layout, paths, args, workdir)
        live = tenant_metrics(paths)
        comparison = compare_metrics(manifest.get("metrics", {}), live)
        payload = {
            "migration_verify": comparison["status"],
            "tenant": paths.name,
            "backup_id": manifest.get("backup_id"),
            "source_write_freeze": manifest.get("migration", {}).get("write_freeze"),
            "comparison": comparison,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if comparison["status"] == "MATCH" else 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vlux-cloud",
        description="Operate VLUX POS Cloud Managed multi-tenant hosts.",
    )
    parser.add_argument("--base-dir", default=str(DEFAULT_BASE_DIR))
    parser.add_argument(
        "--host-id",
        default=os.environ.get("VLUX_CLOUD_HOST_ID"),
        help="Namespace the Docker objects so a second host can be simulated on one machine.",
    )
    parser.add_argument("--version", action="version", version="vlux-cloud " + CLOUD_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("host-init", help="Prepare the host and start VLUX EDGE. Idempotent.")
    p.add_argument("--acme-email", default=os.environ.get("VLUX_ACME_EMAIL", ""))
    p.add_argument("--acme-ca", default=os.environ.get("VLUX_ACME_CA", ""))
    p.add_argument("--caddy-image", default=CADDY_IMAGE)
    p.add_argument(
        "--edge-mode",
        choices=EDGE_MODES,
        default=os.environ.get("VLUX_EDGE_MODE", DEFAULT_EDGE_MODE),
        help="cloudflare_tunnel publishes no host port and leaves TLS to Cloudflare.",
    )
    p.add_argument("--allow-unsupported-os", action="store_true")
    p.add_argument("--timeout", type=int, default=180)
    p.set_defaults(func=host_init)

    p = sub.add_parser("provision", help="Create or reconcile one isolated tenant.")
    p.add_argument("tenant")
    p.add_argument("--domain", required=True)
    p.add_argument("--owner-email", required=True)
    p.add_argument("--edition", choices=("cloud_managed",), default="cloud_managed")
    p.add_argument("--data-class", choices=DATA_CLASSES, default=DEFAULT_DATA_CLASS)
    p.add_argument("--tls-mode", choices=TLS_MODES, default="public")
    p.add_argument("--image", default=None)
    p.add_argument("--postgres-image", default=None)
    p.add_argument("--db-host", default=None, help="Point at managed PostgreSQL instead of the tenant container.")
    p.add_argument("--company-name", default=None)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--max-cron-threads", type=int, default=None)
    p.add_argument("--limit-memory-soft-mb", type=int, default=2048)
    p.add_argument("--limit-memory-hard-mb", type=int, default=2560)
    p.add_argument("--no-readonly-rootfs", action="store_true")
    p.add_argument("--skip-dns-check", action="store_true")
    p.add_argument("--pull", action="store_true")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--https-timeout", type=int, default=180)
    p.set_defaults(func=provision)

    p = sub.add_parser("status", help="Report tenant state without revealing secrets.")
    p.add_argument("tenant", nargs="?")
    p.set_defaults(func=status)

    p = sub.add_parser("health", help="Verify PostgreSQL, Odoo, edge route and HTTPS.")
    p.add_argument("tenant", nargs="?")
    p.set_defaults(func=health)

    p = sub.add_parser("list", help="List tenants provisioned on this host.")
    p.set_defaults(func=list_tenants)

    p = sub.add_parser("backup", help="Consistent database + filestore backup.")
    p.add_argument("tenant")
    p.add_argument("--label", default="manual")
    p.add_argument("--tier", default="daily", choices=("daily", "weekly", "monthly", "migration"))
    p.add_argument("--offsite", action="store_true", help="Also upload to S3-compatible storage.")
    p.add_argument("--retention", action="store_true", help="Prune old local backups afterwards.")
    p.add_argument("--keep-daily", type=int, default=7)
    p.add_argument("--keep-weekly", type=int, default=4)
    p.add_argument("--keep-monthly", type=int, default=3)
    p.add_argument("--timeout", type=int, default=600)
    p.set_defaults(func=backup)

    p = sub.add_parser("restore", help="Restore a verified backup into its own tenant.")
    p.add_argument("tenant")
    p.add_argument("backup", nargs="?", default="")
    p.add_argument("--from-s3", default=None, help="Object key to download instead of a local path.")
    p.add_argument("--confirm", required=True)
    p.add_argument("--no-safety-backup", action="store_true")
    p.add_argument("--timeout", type=int, default=600)
    p.set_defaults(func=restore)

    p = sub.add_parser("upgrade", help="Backup, swap image digest, migrate targeted modules.")
    p.add_argument("tenant")
    p.add_argument("--image", required=True)
    p.add_argument("--allow-unpinned", action="store_true")
    p.add_argument("--pull", action="store_true", help="Force a registry pull even if the image is cached.")
    p.add_argument("--offsite", action="store_true")
    p.add_argument("--timeout", type=int, default=900)
    p.set_defaults(func=upgrade)

    p = sub.add_parser("disable", help="Remove edge traffic and stop the tenant. Never deletes data.")
    p.add_argument("tenant")
    p.add_argument("--stop-database", action="store_true")
    p.set_defaults(func=disable)

    # --- Cloudflare staging -------------------------------------------------
    p = sub.add_parser(
        "staging-init",
        help="Install and start the Cloudflare Tunnel connector for this host.",
    )
    p.add_argument("--tunnel-token-file", required=True)
    p.add_argument("--cloudflared-image", default=None)
    p.add_argument("--allow-unpinned", action="store_true")
    p.add_argument("--pull", action="store_true")
    p.add_argument("--no-wait", action="store_true")
    p.add_argument("--timeout", type=int, default=180)
    p.set_defaults(func=staging_init)

    p = sub.add_parser("tunnel", help="Start, stop or inspect the Cloudflare Tunnel.")
    p.add_argument("action", choices=("status", "start", "stop"))
    p.set_defaults(func=tunnel_command)

    # --- S3-compatible off-site storage -------------------------------------
    p = sub.add_parser("r2", help="Configure or test S3-compatible off-site storage.")
    r2_sub = p.add_subparsers(dest="r2_command", required=True)

    q = r2_sub.add_parser("configure", help="Record the bucket and credential locations.")
    q.add_argument("--provider", default="cloudflare_r2")
    q.add_argument("--account-id", default=None, help="Cloudflare account id; builds the R2 endpoint.")
    q.add_argument("--endpoint", default=None, help="Explicit S3 endpoint for non-R2 providers.")
    q.add_argument("--region", default="auto")
    q.add_argument("--bucket", required=True)
    q.add_argument("--prefix", default="vlux-pos")
    q.add_argument("--access-key-id-file", default=None)
    q.add_argument("--secret-access-key-file", default=None)
    q.set_defaults(func=r2_configure)

    q = r2_sub.add_parser("test", help="Round-trip a synthetic object: write, read, verify, delete.")
    q.set_defaults(func=r2_test)

    # --- maintenance / write freeze -----------------------------------------
    p = sub.add_parser(
        "maintenance",
        help="Freeze or release writes at the edge during a cutover.",
    )
    p.add_argument("action", choices=("enable", "disable"))
    p.add_argument("tenant")
    p.add_argument("--timeout", type=int, default=600)
    p.set_defaults(func=maintenance_command)

    # --- migration ----------------------------------------------------------
    p = sub.add_parser("migration", help="Move a tenant between hosts without data loss.")
    mig = p.add_subparsers(dest="migration_command", required=True)

    q = mig.add_parser("export", help="Cutover-ready export of database, filestore and metrics.")
    q.add_argument("tenant")
    q.add_argument("--allow-live", action="store_true", help="Rehearsal export without a write freeze.")
    q.add_argument("--offsite", action="store_true")
    q.add_argument("--timeout", type=int, default=900)
    q.set_defaults(func=migration_export)

    q = mig.add_parser("precheck", help="Check a bundle against this host before importing.")
    q.add_argument("tenant")
    q.add_argument("backup", nargs="?", default="")
    q.add_argument("--from-s3", default=None, help="Object key to download instead of a local path.")
    q.add_argument("--workdir", default=None)
    q.set_defaults(func=migration_precheck)

    q = mig.add_parser("import", help="Adopt a tenant on this host. Traffic stays disabled.")
    q.add_argument("tenant")
    q.add_argument("backup", nargs="?", default="")
    q.add_argument("--from-s3", default=None)
    q.add_argument("--confirm", required=True)
    q.add_argument("--domain", default=None)
    q.add_argument("--image", default=None)
    q.add_argument("--postgres-image", default=None)
    q.add_argument("--db-host", default=None)
    q.add_argument("--tls-mode", choices=TLS_MODES, default="public")
    q.add_argument("--workers", type=int, default=None)
    q.add_argument("--max-cron-threads", type=int, default=None)
    q.add_argument("--limit-memory-soft-mb", type=int, default=2048)
    q.add_argument("--limit-memory-hard-mb", type=int, default=2560)
    q.add_argument("--no-readonly-rootfs", action="store_true")
    q.add_argument("--allow-incompatible", action="store_true")
    q.add_argument("--workdir", default=None)
    q.add_argument("--timeout", type=int, default=900)
    q.set_defaults(func=migration_import)

    q = mig.add_parser("verify", help="Compare live counts against the bundle's recorded metrics.")
    q.add_argument("tenant")
    q.add_argument("backup", nargs="?", default="")
    q.add_argument("--from-s3", default=None)
    q.add_argument("--workdir", default=None)
    q.set_defaults(func=migration_verify)

    # --- retention ----------------------------------------------------------
    p = sub.add_parser("retention", help="Apply the local backup retention policy.")
    p.add_argument("tenant")
    p.add_argument("--keep-daily", type=int, default=7)
    p.add_argument("--keep-weekly", type=int, default=4)
    p.add_argument("--keep-monthly", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=retention_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return args.func(args)
    except VluxError as exc:
        print("ERROR: " + exc.message, file=sys.stderr)
        return exc.code if isinstance(exc.code, int) else 1


if __name__ == "__main__":
    raise SystemExit(main())
