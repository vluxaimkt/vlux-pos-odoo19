#!/usr/bin/env python3
"""Offline checks for the VLUX POS Cloud Managed target.

These run on any machine with a stdlib Python: no Docker, no PyYAML. They cover
the static half of the cloud security contract, so a broken isolation or secret
rule fails before a single container is built:

* TEMPLATE_RENDER   - every tenant template renders with no placeholder left
* TENANT_ISOLATION_SCAN - no published ports, PostgreSQL off the edge network,
                      per-tenant private network, unique app alias
* CONTAINER_SECRET_SCAN - no secret material baked into the image or templates,
                      runtime stage free of build tooling, app runs non-root
* EDGE_ARCHITECTURE - exactly one Caddy stack owns 80/443
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLOUD = ROOT / "packaging" / "cloud"
TEMPLATES = CLOUD / "templates"

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def load_cli():
    spec = importlib.util.spec_from_file_location("vlux_cloud", CLOUD / "vlux_cloud.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


TENANT = "tenant-a"
TENANT_ROOT = "/srv/vlux-pos/tenants/tenant-a"
DB = "vlux_tenant_a"


def render_all(cli) -> dict[str, str]:
    read_only_block = "    read_only: true\n    tmpfs:\n      - /tmp:size=512m,mode=1777\n"
    compose_text = cli.render(
        TEMPLATES / "tenant-compose.yaml.tmpl",
        {
            "__TENANT_ROOT__": TENANT_ROOT,
            "__PROJECT__": "vlux-" + TENANT,
            "__PG_CONTAINER__": "vlux-" + TENANT + "-postgres",
            "__APP_CONTAINER__": "vlux-" + TENANT + "-app",
            "__APP_ALIAS__": TENANT + "-app",
            "__PRIVATE_NETWORK__": "vlux-" + TENANT + "-private",
            "__EDGE_NETWORK__": "vlux-edge",
            "__TENANT__": TENANT,
            "__READ_ONLY_BLOCK__": read_only_block,
        },
    )
    caddy_text = cli.render(
        TEMPLATES / "tenant.caddy.tmpl",
        {
            "__SITE_ADDRESS__": "pos.example.test",
            "__TLS_LINE__": "\ttls internal\n",
            "__APP_ALIAS__": TENANT + "-app",
            "__TENANT__": TENANT,
            "__HTTP_PORT__": "8069",
            "__GEVENT_PORT__": "8072",
        },
    )
    conf_text = cli.render(
        TEMPLATES / "odoo.conf.tmpl",
        {
            "__VLUX_ADMIN_PASSWD__": "PLACEHOLDER-MASTER",
            "__DB_HOST__": "postgres",
            "__DB_PORT__": "5432",
            "__DB_USER__": DB,
            "__VLUX_DB_PASSWORD__": "PLACEHOLDER-DB",
            "__DB_NAME__": DB,
            "__DB_MAXCONN__": "24",
            "__HTTP_PORT__": "8069",
            "__GEVENT_PORT__": "8072",
            "__WORKERS__": "2",
            "__MAX_CRON_THREADS__": "1",
            "__LIMIT_MEMORY_SOFT__": "2147483648",
            "__LIMIT_MEMORY_HARD__": "2684354560",
        },
    )
    edge_text = cli.render(
        CLOUD / "edge" / "Caddyfile.tmpl",
        {"__ACME_EMAIL_LINE__": "\temail ops@example.test\n", "__ACME_CA_LINE__": ""},
    )
    maintenance_text = cli.render(
        TEMPLATES / "tenant-maintenance.caddy.tmpl",
        {
            "__SITE_ADDRESS__": "http://pos-demo.example.com",
            "__TLS_LINE__": "",
            "__TENANT__": TENANT,
        },
    )
    cloudflared_text = cli.render(
        TEMPLATES / "cloudflared-compose.yaml.tmpl",
        {
            "__PROJECT__": "vlux-tunnel",
            "__TUNNEL_CONTAINER__": "vlux-cloudflared",
            "__EDGE_NETWORK__": "vlux-edge",
            "__METRICS_PORT__": "2000",
            "__TOKEN_FILE__": "/srv/vlux-pos/secrets/cloudflare_tunnel_token",
        },
    )
    return {
        "compose": compose_text,
        "caddy": caddy_text,
        "conf": conf_text,
        "edge": edge_text,
        "maintenance": maintenance_text,
        "cloudflared": cloudflared_text,
        "edge_compose_public": render_edge_compose(cli, "public_acme"),
        "edge_compose_tunnel": render_edge_compose(cli, "cloudflare_tunnel"),
    }


def check_validation(cli) -> None:
    for good in ("tenant-a", "braille", "cliente-2"):
        try:
            cli.validate_tenant(good)
        except SystemExit:
            FAILURES.append("valid tenant slug rejected: " + good)
    for bad in ("A", "-x", "x-", "ab", "edge", "tenant_a", "x" * 40):
        try:
            cli.validate_tenant(bad)
        except SystemExit:
            continue
        FAILURES.append("invalid tenant slug accepted: " + bad)

    for good in ("pos.cliente.com", "cloud-test.vlux.lab"):
        try:
            cli.validate_domain(good)
        except SystemExit:
            FAILURES.append("valid domain rejected: " + good)
    for bad in ("localhost", "*.cliente.com", "-bad.com", "a..b.com"):
        try:
            cli.validate_domain(bad)
        except SystemExit:
            continue
        FAILURES.append("invalid domain accepted: " + bad)

    check(cli.db_identifier("tenant-a") == "vlux_tenant_a", "db identifier derivation changed")
    check(cli.CLOUD_DB_TARGET == "16.15", "CLOUD_DB_TARGET must be 16.15")
    check(cli.CLOUD_DB_MAJOR == "16", "CLOUD_DB_MAJOR must be 16")
    check("postgres:16.15@sha256:" in cli.POSTGRES_IMAGE, "PostgreSQL image must be 16.15 pinned by digest")
    check("latest" not in cli.POSTGRES_IMAGE, "PostgreSQL image must not use latest")
    check(cli.GEVENT_PORT == 8072, "gevent port must be 8072")
    check(cli.DEFAULT_WORKERS == 2, "default workers must be 2 for small tenants")


def check_tenant_isolation(rendered: dict[str, str]) -> None:
    compose_text = rendered["compose"]

    check(
        not re.search(r"(?m)^\s*ports:", compose_text),
        "TENANT_ISOLATION: tenant compose must not publish any host port",
    )
    for port in ("5432:", "8069:", "8072:"):
        check(
            port not in compose_text,
            "TENANT_ISOLATION: tenant compose must not map host port " + port,
        )

    services = {}
    current = None
    for line in compose_text.splitlines():
        match = re.match(r"^  ([a-z0-9_-]+):\s*$", line)
        if match and not line.startswith("    "):
            current = match.group(1)
            services[current] = []
        elif current is not None and (line.startswith("    ") or not line.strip()):
            services[current].append(line)
        elif line and not line.startswith(" "):
            current = None

    check("postgres" in services and "app" in services, "TENANT_ISOLATION: expected postgres and app services")
    postgres_block = "\n".join(services.get("postgres", []))
    app_block = "\n".join(services.get("app", []))

    check(
        "edge" not in postgres_block,
        "TENANT_ISOLATION: PostgreSQL must never join the shared edge network",
    )
    check(
        "tenant_private" in postgres_block,
        "TENANT_ISOLATION: PostgreSQL must join the tenant private network",
    )
    check(
        "tenant_private" in app_block and "edge" in app_block,
        "TENANT_ISOLATION: app must join both the private and the edge network",
    )
    check(
        TENANT + "-app" in app_block,
        "TENANT_ISOLATION: app needs the unique per-tenant edge alias <tenant>-app",
    )
    check(
        re.search(r"(?m)^\s*name: vlux-" + TENANT + r"-private\s*$", compose_text) is not None,
        "TENANT_ISOLATION: private network must be named vlux-<tenant>-private",
    )
    check(
        re.search(r"(?m)^\s*internal: true\s*$", compose_text) is not None,
        "TENANT_ISOLATION: the tenant private network must be internal",
    )
    check(
        re.search(r"(?m)^\s*name: vlux-edge\s*$", compose_text) is not None
        and re.search(r"(?m)^\s*external: true\s*$", compose_text) is not None,
        "TENANT_ISOLATION: the edge network must be referenced as an external network",
    )
    check(
        "file: " + TENANT_ROOT + "/secrets/db_password" in compose_text,
        "TENANT_ISOLATION: the database password must be a file-based Docker secret",
    )

    for needle, message in (
        ("no-new-privileges:true", "app/postgres must set no-new-privileges"),
        ("cap_drop", "app/postgres must drop capabilities"),
        ("max-size", "docker log rotation limits must be configured"),
        ("read_only: true", "read-only rootfs block must render when requested"),
    ):
        check(needle in compose_text, "CONTAINER_SECURITY: " + message)

    check(
        "privileged" not in compose_text,
        "CONTAINER_SECURITY: privileged containers are forbidden",
    )
    check(
        "/var/run/docker.sock" not in compose_text,
        "CONTAINER_SECURITY: the Docker socket must never be mounted into a tenant",
    )
    check(
        "network_mode: host" not in compose_text,
        "CONTAINER_SECURITY: host networking is forbidden",
    )


def check_odoo_conf(rendered: dict[str, str]) -> None:
    conf = rendered["conf"]
    required = {
        "db_host = postgres": "db_host must point at the tenant-private PostgreSQL alias",
        "dbfilter = ^" + DB + "$": "dbfilter must pin the exact tenant database",
        "list_db = False": "the database manager must be disabled",
        "proxy_mode = True": "proxy_mode must be enabled behind the edge",
        "http_port = 8069": "http_port must be 8069",
        "gevent_port = 8072": "gevent_port must be 8072",
        "workers = 2": "workers must be configurable and default to 2",
        "max_cron_threads = 1": "max_cron_threads must be configurable",
        "data_dir = /var/lib/vlux-pos": "data_dir must live on the tenant volume",
        "/opt/vlux/pos/addons": "addons_path must include the VLUX addons",
    }
    for needle, message in required.items():
        check(needle in conf, "ODOO_CONFIG: " + message)


def render_edge_compose(cli, edge_mode: str) -> str:
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
    return cli.render(
        CLOUD / "edge" / "compose.yaml.tmpl",
        {
            "__PROJECT__": "vlux-edge",
            "__EDGE_CONTAINER__": "vlux-edge-caddy",
            "__EDGE_NETWORK__": "vlux-edge",
            "__EDGE_ROOT__": "/srv/vlux-pos/edge",
            "__EDGE_MODE__": edge_mode,
            "__PORTS_BLOCK__": ports_block,
        },
    )


def check_edge(rendered: dict[str, str]) -> None:
    edge_compose = rendered["edge_compose_public"]
    check('"80:80"' in edge_compose, "EDGE: the edge must publish 80")
    check('"443:443"' in edge_compose, "EDGE: the edge must publish 443")
    check(
        edge_compose.count('"') >= 4 and "5432" not in edge_compose,
        "EDGE: the edge must not publish a database port",
    )
    check(
        "external: true" in edge_compose and "name: vlux-edge" in edge_compose,
        "EDGE: the edge must attach to the shared external vlux-edge network",
    )
    check(
        "caddy:2.10@sha256:" in edge_compose,
        "EDGE: the Caddy image must be pinned by digest",
    )
    check(
        "import /etc/caddy/tenants/*.caddy" in rendered["edge"],
        "EDGE: the global Caddyfile must import per-tenant route files",
    )

    caddy = rendered["caddy"]
    check(
        "reverse_proxy " + TENANT + "-app:8072" in caddy,
        "WEBSOCKET: /websocket must proxy to the tenant gevent port",
    )
    check(
        "reverse_proxy " + TENANT + "-app:8069" in caddy,
        "EDGE_ROUTING: default traffic must proxy to the tenant HTTP port",
    )
    check("@websocket path /websocket" in caddy, "WEBSOCKET: missing websocket matcher")
    check("app:8069" not in caddy.replace(TENANT + "-app:8069", ""),
          "EDGE_ROUTING: routes must use the unique <tenant>-app alias, never a bare app alias")


def check_dockerfile() -> None:
    text = (CLOUD / "Dockerfile").read_text(encoding="utf-8")
    stages = text.split("FROM ${PYTHON_BASE} AS runtime")
    check(len(stages) == 2, "IMAGE: Dockerfile must be multi-stage with a runtime stage")
    builder, runtime = stages[0], stages[1] if len(stages) == 2 else ""

    check("AS builder" in builder, "IMAGE: missing builder stage")
    check("build-essential" in builder, "IMAGE: the builder stage should carry build tooling")
    for tool in ("build-essential", "libpq-dev", "libsasl2-dev", "libxslt1-dev"):
        check(tool not in runtime, "IMAGE: runtime stage must not install " + tool)
    check(
        re.search(r"(?m)^\s*git\s*\\?\s*$", runtime) is None and " git " not in runtime,
        "IMAGE: runtime stage must not contain git (images are immutable)",
    )
    check("USER vlux-pos" in runtime, "IMAGE: the app must run as the non-root vlux-pos user")
    check(
        runtime.rstrip().rfind("USER vlux-pos") < runtime.rstrip().rfind("ENTRYPOINT"),
        "IMAGE: USER must be set before the entrypoint",
    )
    check("rm -rf \"$ODOO_HOME/.git\"" in builder, "IMAGE: the Odoo .git tree must be removed")
    check(
        "a2d73c5900d8886d115afe1ccb7f5c97c7e71a97" in text,
        "IMAGE: the pinned Odoo commit must not change",
    )
    check("python:3.12.10-slim-bookworm@sha256:" in text, "IMAGE: the base image must be digest pinned")
    check("HEALTHCHECK" in runtime, "IMAGE: a real healthcheck is required")
    check("/vlux/health" in runtime, "IMAGE: the healthcheck must hit /vlux/health")
    for label in (
        "org.opencontainers.image.version",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.created",
        "vlux.odoo.commit",
    ):
        check(label in text, "OCI_METADATA: missing label " + label)


def check_container_secrets(rendered: dict[str, str]) -> None:
    """No credential material may live in the repo-tracked cloud payload."""
    secret_like = re.compile(
        r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['\"]?([A-Za-z0-9+/_=-]{12,})"
    )
    allow = (
        "PASSWORD_FILE",
        "PLACEHOLDER",
        "__VLUX_",
        "__VLUX_DB_PASSWORD__",
        "__VLUX_ADMIN_PASSWD__",
        "db_password",
        "token_urlsafe",
        "secret_access_key",
        "access_key_id",
        "session_token",
        "VLUX_OFFSITE_",
        "example",
    )
    for path in sorted(CLOUD.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in secret_like.finditer(text):
            line = match.group(0)
            if any(marker.lower() in line.lower() for marker in allow):
                continue
            FAILURES.append(
                "CONTAINER_SECRET_SCAN: possible credential in "
                + str(path.relative_to(ROOT))
                + ": "
                + match.group(1)
            )
        if "BEGIN RSA PRIVATE KEY" in text or "BEGIN PRIVATE KEY" in text:
            FAILURES.append("PRIVATE_KEY_EXPOSURE: key material in " + str(path.relative_to(ROOT)))

    dockerfile = (CLOUD / "Dockerfile").read_text(encoding="utf-8")
    for forbidden in ("secrets/", "tenant.json", ".env", "filestore", "backups"):
        check(
            "COPY " + forbidden not in dockerfile,
            "CONTAINER_SECRET_SCAN: the image must not COPY " + forbidden,
        )
    check(
        "ghcr.io" not in rendered["compose"],
        "IMMUTABLE_IMAGE: the tenant compose must take its image from .env, not a literal tag",
    )


SAMPLE_ARGV = {
    "host_init": ["host-init"],
    "provision": ["provision", "tenant-a", "--domain", "pos.example.test",
                  "--owner-email", "owner@example.test"],
    "status": ["status"],
    "health": ["health"],
    "list_tenants": ["list"],
    "backup": ["backup", "tenant-a"],
    "restore": ["restore", "tenant-a", "backup.tar.gz", "--confirm", "RESTORE_TENANT"],
    "upgrade": ["upgrade", "tenant-a", "--image", "repo@sha256:" + "0" * 64],
    "disable": ["disable", "tenant-a"],
    "staging_init": ["staging-init", "--tunnel-token-file", "/srv/vlux-pos/secrets/tok"],
    "tunnel_command": ["tunnel", "status"],
    "r2_configure": ["r2", "configure", "--bucket", "vlux-pos-backups", "--account-id", "acct"],
    "r2_test": ["r2", "test"],
    "maintenance_command": ["maintenance", "enable", "tenant-a"],
    "migration_export": ["migration", "export", "tenant-a"],
    "migration_precheck": ["migration", "precheck", "tenant-a", "b.tar.gz"],
    "migration_import": [
        "migration", "import", "tenant-a", "b.tar.gz", "--confirm", "IMPORT_TENANT",
    ],
    "migration_verify": ["migration", "verify", "tenant-a", "b.tar.gz"],
    "retention_command": ["retention", "tenant-a"],
}


def check_cli_surface(cli) -> None:
    """Every args.<x> a handler reads must exist on its parsed namespace.

    A subcommand that forgets an option only fails at runtime, halfway through
    provisioning a live tenant, so it is caught here instead.
    """
    tree = ast.parse((CLOUD / "vlux_cloud.py").read_text(encoding="utf-8"))
    reads: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in SAMPLE_ARGV:
            names = set()
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Attribute)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "args"
                ):
                    names.add(child.attr)
            reads[node.name] = names

    missing_handlers = set(SAMPLE_ARGV) - set(reads)
    check(not missing_handlers, "CLI: handlers not found in the module: " + ", ".join(sorted(missing_handlers)))

    parser = cli.build_parser()
    for handler, argv in SAMPLE_ARGV.items():
        try:
            namespace = parser.parse_args(argv)
        except SystemExit:
            FAILURES.append("CLI: cannot parse sample argv for " + handler + ": " + " ".join(argv))
            continue
        check(
            getattr(namespace, "func", None) is not None,
            "CLI: subcommand " + argv[0] + " has no handler bound",
        )
        for attr in sorted(reads.get(handler, set())):
            check(
                hasattr(namespace, attr),
                "CLI: " + handler + " reads args." + attr + " but " + argv[0] + " does not define it",
            )

    for command in ("host-init", "provision", "status", "health", "list",
                    "backup", "restore", "upgrade", "disable"):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                parser.parse_args([command, "--help"])
            except SystemExit as exc:
                check(exc.code == 0, "CLI: --help failed for " + command)


def check_odoo_api_surface(cli) -> None:
    """The Owner bootstrap runs inside Odoo 19, so its API names must be real.

    Odoo 19 renamed res.users.groups_id to group_ids; the VLUX addon tests are
    the living reference, so they are cross-checked here rather than trusted
    from memory.
    """
    source = (CLOUD / "vlux_cloud.py").read_text(encoding="utf-8")
    check(
        "'groups_id'" not in source,
        "ODOO_API: res.users uses 'group_ids' on Odoo 19, not 'groups_id'",
    )
    check("'group_ids'" in source, "ODOO_API: the Owner bootstrap must assign group_ids")

    roles_test = ROOT / "vlux_core" / "tests" / "test_roles.py"
    if roles_test.exists():
        check(
            "group_ids" in roles_test.read_text(encoding="utf-8"),
            "ODOO_API: vlux_core tests no longer use group_ids; recheck the Owner bootstrap",
        )

    check(
        cli.OWNER_GROUP_XMLID.startswith("vlux_core."),
        "ODOO_API: the Owner group must come from vlux_core",
    )
    security = ROOT / "vlux_core" / "security" / "security.xml"
    if security.exists():
        group_id = cli.OWNER_GROUP_XMLID.split(".", 1)[1]
        check(
            'id="' + group_id + '"' in security.read_text(encoding="utf-8"),
            "ODOO_API: " + cli.OWNER_GROUP_XMLID + " is not defined in vlux_core/security/security.xml",
        )

    for addon in cli.PRODUCTIVE_ADDONS:
        check(
            (ROOT / addon / "__manifest__.py").exists(),
            "ODOO_API: productive addon " + addon + " is missing from the repository",
        )
    check(
        "vlux_facturacion" not in cli.PRODUCTIVE_ADDONS,
        "ODOO_API: vlux_facturacion must not be installed by cloud provisioning",
    )


def check_tunnel(cli, rendered: dict[str, str]) -> None:
    """The tunnel must be outbound-only, edge-scoped and token-file based."""
    cf = rendered["cloudflared"]

    check(
        not re.search(r"(?m)^\s*ports:", cf),
        "TUNNEL: the cloudflared stack must publish no host port",
    )
    check(
        "--token-file" in cf and "/run/secrets/cloudflare_tunnel_token" in cf,
        "TUNNEL: the tunnel token must be supplied as a file, not inline",
    )
    check(
        "TUNNEL_TOKEN=" not in cf and "--token " not in cf,
        "TUNNEL_TOKEN_LEAK: the token must never be an environment variable or argv value",
    )
    check(
        "-private" not in cf,
        "TUNNEL: cloudflared must not be attached to any tenant private network",
    )
    check(
        cf.count("networks:") >= 1 and "__" not in cf,
        "TUNNEL: the cloudflared template must render completely",
    )
    network_names = re.findall(r"(?m)^\s{4}name: (\S+)$", cf)
    check(
        network_names == ["vlux-edge"],
        "TUNNEL: cloudflared must join only the shared edge network, found " + str(network_names),
    )
    for needle, message in (
        ("read_only: true", "cloudflared must run on a read-only root filesystem"),
        ("no-new-privileges:true", "cloudflared must set no-new-privileges"),
        ("cap_drop", "cloudflared must drop capabilities"),
        ("max-size", "cloudflared logs must be rotated"),
        ("--no-autoupdate", "cloudflared must not self-update inside the container"),
    ):
        check(needle in cf, "TUNNEL: " + message)
    check(
        "loglevel" in cf and "debug" not in cf,
        "TUNNEL: the default log level must be info, never debug",
    )

    check(
        "cloudflare/cloudflared:" in cli.CLOUDFLARED_IMAGE
        and "@sha256:" in cli.CLOUDFLARED_IMAGE,
        "TUNNEL: the cloudflared image must be pinned by version and digest",
    )
    check("latest" not in cli.CLOUDFLARED_IMAGE, "TUNNEL: cloudflared must not use latest")
    check(
        cli.CLOUDFLARED_UID != 0,
        "TUNNEL: cloudflared must not be expected to run as root",
    )

    edge_tunnel = rendered["edge_compose_tunnel"]
    check(
        not re.search(r"(?m)^\s*ports:", edge_tunnel),
        "TUNNEL: in cloudflare_tunnel mode the edge must publish no host port",
    )
    for port in ("80:80", "443:443", "5432", "8069", "8072"):
        check(
            port not in edge_tunnel,
            "TUNNEL: tunnel-mode edge must not map " + port,
        )

    site, tls = cli.route_site_address("pos-demo.example.com", "cloudflare_tunnel", "public")
    check(
        site == "http://pos-demo.example.com" and tls == "",
        "TUNNEL: tunnel-mode routes must be plain http:// with no ACME",
    )
    maintenance = rendered["maintenance"]
    check(
        "503" in maintenance and "reverse_proxy" not in maintenance,
        "MAINTENANCE: the maintenance route must answer 503 and proxy nothing",
    )


def check_staging_secret_scans() -> None:
    """CLOUDFLARE_TOKEN_LEAK_SCAN and R2_SECRET_LEAK_SCAN over the repository."""
    # Cloudflare tunnel tokens are long base64 JSON blobs and always start eyJ.
    token_like = re.compile(r"eyJ[A-Za-z0-9_\-]{60,}")
    forbidden_names = {
        "cloudflare_tunnel_token",
        "r2_access_key_id",
        "r2_secret_access_key",
    }
    assignment = re.compile(
        r"(?i)(r2[_-]?(access[_-]?key[_-]?id|secret[_-]?access[_-]?key)|tunnel[_-]?token)"
        r"\s*[:=]\s*['\"]?([A-Za-z0-9+/_=-]{20,})"
    )
    allow = ("REEMPLAZAR", "PLACEHOLDER", "__VLUX", "<", "example", "ACCOUNT_ID",
             "ACCESS_KEY", "SECRET_KEY", "your-", "file", "path")

    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        parts = set(path.relative_to(ROOT).parts)
        if parts & {".git", "__pycache__", "dist", "node_modules"}:
            continue
        if path.name in forbidden_names:
            FAILURES.append(
                "CLOUDFLARE_TOKEN_LEAK/R2_SECRET_LEAK: credential file committed: "
                + str(path.relative_to(ROOT))
            )
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pyc", ".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in token_like.finditer(text):
            FAILURES.append(
                "CLOUDFLARE_TOKEN_LEAK_SCAN: tunnel-token-shaped value in "
                + str(path.relative_to(ROOT)) + ": " + match.group(0)[:12] + "..."
            )
        for match in assignment.finditer(text):
            line = match.group(0)
            if any(marker.lower() in line.lower() for marker in allow):
                continue
            FAILURES.append(
                "R2_SECRET_LEAK_SCAN: credential-shaped assignment in "
                + str(path.relative_to(ROOT)) + ": " + match.group(1)
            )


def main() -> int:
    cli = load_cli()
    # vlux_cloud.fail() raises a SystemExit carrying an int code, which Python
    # exits on silently. Surface the message instead of dying with no output.
    try:
        rendered = render_all(cli)
    except SystemExit as exc:
        print("ERROR: " + str(getattr(exc, "message", exc)), file=sys.stderr)
        print("CLOUD_STATIC_CHECKS=FAIL", file=sys.stderr)
        return 1
    check_validation(cli)
    check_tenant_isolation(rendered)
    check_odoo_conf(rendered)
    check_edge(rendered)
    check_dockerfile()
    check_container_secrets(rendered)
    check_cli_surface(cli)
    check_odoo_api_surface(cli)
    check_tunnel(cli, rendered)
    check_staging_secret_scans()

    if FAILURES:
        for failure in FAILURES:
            print("ERROR: " + failure, file=sys.stderr)
        print("CLOUD_STATIC_CHECKS=FAIL", file=sys.stderr)
        return 1
    print("TEMPLATE_RENDER=PASS")
    print("TENANT_ISOLATION_SCAN=PASS")
    print("CONTAINER_SECRET_SCAN=PASS")
    print("EDGE_ARCHITECTURE=PASS")
    print("CLI_SURFACE=PASS")
    print("ODOO_API_SURFACE=PASS")
    print("CLOUDFLARE_TUNNEL_STATIC_CHECK=PASS")
    print("CLOUDFLARE_TOKEN_LEAK_SCAN=PASS")
    print("R2_SECRET_LEAK_SCAN=PASS")
    print("CLOUD_STATIC_CHECKS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
