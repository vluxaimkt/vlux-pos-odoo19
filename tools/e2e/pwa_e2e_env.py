"""Disposable Odoo for the register PWA browser tests (frontend/pos/e2e).

Builds a brand-new database the way a real tenant is built, then seeds only
synthetic data, so the Playwright Golden Path runs against real Odoo with the
real VLUX API (nothing simulated):

1. install the productive addons without demo data (as ``vlux-cloud provision``);
2. Mexican chart of accounts (``localisation_script`` of packaging/cloud/vlux_cloud.py);
3. store, register and employees (``tools/onboarding/configure_store.py``);
4. synthetic products, a register user, one register token per browser project
   and a JSON-2 API key the tests use to read back what Odoo stored.

Everything the tests need (tokens, PINs, the API key) is generated here and
written to the fixture file only; it is never printed. Run it with the Python
that runs Odoo:

    python tools/e2e/pwa_e2e_env.py prepare --odoo-home ../odoo \\
        --addons-path ../odoo/addons,../odoo/odoo/addons,. \\
        -d vlux_pwa_e2e_test --fixture frontend/pos/e2e/.fixture.json

Then serve it (no cron jobs) and run the tests:

    python tools/e2e/pwa_e2e_env.py serve ... -d vlux_pwa_e2e_test --port 8069
    cd frontend/pos && npm run e2e

Safety: it refuses a database whose name does not end in ``_test``, a
PostgreSQL host that is not this machine, and a database that already exists
(a fresh one per run; nothing is ever dropped or overwritten).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
# Productive addons of a tenant, in the order the cloud installs them.
ADDONS = "vlux_core,vlux_mobile_scanner,vlux_owner,vlux_pos_catalog,vlux_pos_credit,vlux_pos_api,vlux_pos_app"
SCOPES = "system:read catalog:read orders:write session:manage"
PROJECTS = ("desktop", "mobile")
# vlux_core/controllers/api_catalog.py: SETTLE_SECONDS
SETTLE_SECONDS = 30


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pin(taken: set[str]) -> str:
    """A random 4-digit PIN that is not trivial and not already used."""
    while True:
        pin = "%04d" % secrets.randbelow(10000)
        if pin not in taken and len(set(pin)) > 1 and pin not in {"1234", "4321", "0123"}:
            taken.add(pin)
            return pin


def refuse_unsafe(db: str, host: str) -> None:
    if not db.endswith("_test"):
        raise SystemExit("Refusing: the database name must end in _test (got %r)." % db)
    if host not in LOCAL_HOSTS:
        raise SystemExit("Refusing: PostgreSQL must be local (PGHOST=%r)." % host)


def connection_params(config: str) -> dict:
    """PostgreSQL settings as Odoo would use them: the config file, then PG* variables."""
    params = {}
    if config:
        import configparser

        parser = configparser.ConfigParser()
        parser.read(config, encoding="utf-8")
        options = parser["options"] if parser.has_section("options") else {}
        for key in ("host", "port", "user", "password"):
            value = options.get("db_" + key, "")
            if value and value != "False":
                params[key] = value
    for key, env in (("host", "PGHOST"), ("port", "PGPORT"), ("user", "PGUSER"), ("password", "PGPASSWORD")):
        if os.environ.get(env):
            params[key] = os.environ[env]
    return params


def database_exists(db: str, params: dict) -> bool:
    import psycopg2  # Odoo's own driver: present wherever Odoo runs.

    with psycopg2.connect(dbname="postgres", **params) as conn, conn.cursor() as cr:
        cr.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
        return cr.fetchone() is not None


class Odoo:
    def __init__(self, args):
        self.bin = [sys.executable, str(Path(args.odoo_home) / "odoo-bin")]
        self.options = (["-c", args.config] if args.config else []) + ["--addons-path", args.addons_path, "-d", args.database]

    def run(self, extra: list[str]) -> None:
        subprocess.run(self.bin + self.options + extra, check=True)

    def shell(self, script: str, expect: str) -> None:
        # The subcommand goes first: ``odoo-bin shell <options>``.
        result = subprocess.run(
            self.bin + ["shell"] + self.options + ["--no-http", "--log-level=warn"],
            input=script, text=True, encoding="utf-8", errors="replace", capture_output=True,
            # Windows consoles default to cp1252; the shell must read and write UTF-8.
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        # The output can carry the store report; it never contains secrets
        # (the seed writes those to the fixture file only).
        sys.stdout.write(result.stdout)
        if result.returncode or expect not in result.stdout:
            sys.stderr.write(result.stderr[-4000:])
            raise SystemExit("Odoo shell step failed (expected %s)." % expect)


SEED = r'''
import json
from datetime import timedelta
from odoo import fields

PLAN = json.loads(%(plan)r)
env = env(context=dict(env.context, lang="es_MX", tz="America/Mexico_City"))
assert env.cr.dbname.endswith("_test"), "seed only runs on a _test database"
company = env.company
config = env["pos.config"].search([("name", "=", PLAN["register"]), ("company_id", "=", company.id)], limit=1)
assert config, "configure_store did not create the register"
assert not env["pos.order"].search_count([]), "the database already has sales"

# Synthetic products with the company's sale tax (prices include it).
tax = company.account_sale_tax_id
products = []
for item in PLAN["products"]:
    template = env["product.template"].create({
        "name": item["name"], "list_price": item["price"], "barcode": item["barcode"],
        "available_in_pos": True, "is_storable": True, "taxes_id": [(6, 0, tax.ids)],
    })
    products.append(dict(item, id=template.product_variant_id.id))

# The register user the tokens act as (VLUX cashier role) and one token per browser project.
# An internal user, as the tests of vlux_core make them (base.group_user + the VLUX role).
user = env["res.users"].with_context(no_reset_password=True).create({
    "name": "Caja E2E", "login": "caja-e2e", "company_id": company.id, "company_ids": [(6, 0, company.ids)],
    "group_ids": [(6, 0, [env.ref("base.group_user").id, env.ref("vlux_core.group_vlux_cashier").id])],
})
tokens = {}
for project in PLAN["projects"]:
    _token, raw = env["vlux.api.token"].issue(
        "PWA E2E " + project, PLAN["scopes"], user=user, pos_config=config, lifetime_days=30,
    )
    tokens[project] = raw

# Read-back key for the tests (JSON-2 as the administrator of this throwaway database).
admin = env.ref("base.user_admin")
api_key = env["res.users.apikeys"].with_user(admin)._generate(
    "rpc", "PWA E2E read-back", fields.Datetime.now() + timedelta(days=1))

def person(name):
    employee = env["hr.employee"].search([("name", "=", name), ("company_id", "=", company.id)], limit=1)
    return {"id": employee.id, "name": employee.name, "pin": PLAN["pins"][name]}

cash = config.payment_method_ids.filtered("is_cash_count")[:1]
card = config.payment_method_ids.filtered(lambda m: m.journal_id.type == "bank")[:1]
fixture = {
    "database": env.cr.dbname,
    "register": {"id": config.id, "name": config.name, "difference_limit": config.amount_authorized_diff},
    "cashier": person(PLAN["cashier"]),
    "manager": person(PLAN["manager"]),
    "products": products,
    "payment_methods": {"cash": cash.id, "card": card.id},
    "tokens": tokens,
    "api_key": api_key,
    "refunds_need_manager": config.vlux_refunds_need_manager,
}
with open(PLAN["fixture"], "w", encoding="utf-8") as handle:
    json.dump(fixture, handle, ensure_ascii=False, indent=2)
env.cr.commit()
print("VLUX_E2E_SEEDED register=%%s products=%%d tokens=%%d" %% (config.id, len(products), len(tokens)))
'''


def prepare(args) -> None:
    params = connection_params(args.config)
    refuse_unsafe(args.database, params.get("host", ""))
    if database_exists(args.database, params):
        raise SystemExit("Refusing: database %r already exists; use a new name." % args.database)
    cloud = _load(ROOT / "packaging" / "cloud" / "vlux_cloud.py", "vlux_cloud")
    onboarding = _load(ROOT / "tools" / "onboarding" / "configure_store.py", "configure_store")
    odoo = Odoo(args)

    print("[e2e] installing %s into %s" % (ADDONS, args.database), flush=True)
    odoo.run(["-i", ADDONS, "--without-demo=all", "--stop-after-init", "--no-http", "--log-level=warn"])
    print("[e2e] Mexican chart of accounts", flush=True)
    odoo.shell(cloud.localisation_script("MX"), "VLUX_LOCALISATION=MX")

    taken: set[str] = set()
    cashier, manager = "Ana Cajera E2E", "Luis Encargado E2E"
    pins = {cashier: _pin(taken), manager: _pin(taken)}
    store = {
        "company": {"name": "Abarrotes E2E", "vat": "XAXX010101000",
                    "fiscal_regime": "626 - Régimen Simplificado de Confianza",
                    "receipt_legend": "Gracias por su compra (prueba)", "city": "Toluca", "zip": "50000",
                    "state_code": "MEX"},
        "timezone": "America/Mexico_City",
        "prices_include_tax": True,
        "register": {"name": "Caja E2E", "difference_limit": 30},
        "owner_pin": _pin(taken),
        "managers": [{"name": manager, "pin": pins[manager]}],
        "cashiers": [{"name": cashier, "pin": pins[cashier]}],
        "invoicing": False,
    }
    print("[e2e] store, register and employees (configure_store.py)", flush=True)
    odoo.shell(onboarding.SCRIPT % {"payload": json.dumps(store, ensure_ascii=False)}, "VLUX_STORE_READY")

    fixture = Path(args.fixture).resolve()
    fixture.parent.mkdir(parents=True, exist_ok=True)
    plan = {
        "register": store["register"]["name"],
        "products": [
            {"name": "Refresco E2E 600 ml", "price": 20.0, "barcode": "7500000000017"},
            {"name": "Galletas E2E", "price": 15.5, "barcode": "7500000000024"},
        ],
        "scopes": SCOPES,
        "projects": list(PROJECTS),
        "cashier": cashier,
        "manager": manager,
        "pins": pins,
        "fixture": str(fixture),
    }
    print("[e2e] synthetic products, tokens and read-back key", flush=True)
    odoo.shell(SEED % {"plan": json.dumps(plan, ensure_ascii=False)}, "VLUX_E2E_SEEDED")
    print("[e2e] fixture written to %s" % fixture, flush=True)
    # The catalog feed holds back records written in the last SETTLE_SECONDS
    # (vlux_core/controllers/api_catalog.py); a register linked sooner would
    # only see the new products at its next sync, five minutes later.
    print("[e2e] waiting %ds for the catalog settle window" % (SETTLE_SECONDS + 2), flush=True)
    time.sleep(SETTLE_SECONDS + 2)


def serve(args) -> None:
    """Serve only the disposable database, without cron jobs (nothing runs behind the tests' back)."""
    refuse_unsafe(args.database, connection_params(args.config).get("host", ""))
    odoo = Odoo(args)
    command = odoo.bin + odoo.options + [
        "--http-port", str(args.port), "--http-interface", "127.0.0.1",
        "--db-filter", "^%s$" % args.database, "--max-cron-threads", "0", "--log-level", "warn",
    ]
    raise SystemExit(subprocess.call(command))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="Create and seed a fresh disposable database.")
    p.add_argument("--odoo-home", required=True)
    p.add_argument("--addons-path", required=True)
    p.add_argument("-c", "--config", default="")
    p.add_argument("-d", "--database", required=True)
    p.add_argument("--fixture", required=True)
    s = sub.add_parser("serve", help="Run Odoo on the disposable database for the browser tests.")
    s.add_argument("--odoo-home", required=True)
    s.add_argument("--addons-path", required=True)
    s.add_argument("-c", "--config", default="")
    s.add_argument("-d", "--database", required=True)
    s.add_argument("--port", type=int, default=8069)
    args = parser.parse_args()
    {"prepare": prepare, "serve": serve}[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
