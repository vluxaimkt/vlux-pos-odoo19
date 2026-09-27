"""Leave a freshly provisioned VLUX tenant ready to sell, in one idempotent step.

Reads a store description (JSON) and prints an Odoo shell script that applies
it. The script is piped into the tenant's container, so nothing here needs
network access to the tenant or its credentials:

    python tools/onboarding/configure_store.py store.json \
      | docker exec -i vlux-<tenant>-app /opt/vlux/pos/venv/bin/python \
        /opt/vlux/pos/odoo/odoo-bin shell -c /etc/vlux-pos/odoo.conf -d <db> --no-http

What it sets (running it again changes only what differs):

* Spanish (Mexico) installed; the company's users in es_MX and the store's
  timezone.
* Company fiscal identity for the receipt: legal name, RFC, fiscal regime,
  address, phone, email and receipt legend.
* Shelf prices include tax (``prices_include_tax``, default true). Odoo
  refuses this change once the company has accounting entries, so it must run
  before the first sale.
* One register (``register.name``) with cash and card, employee login with
  PIN, prices shown with tax, and the closing difference limit
  (``register.difference_limit``, enforced by the server since vlux_core
  19.0.1.5.0).
* Cashier employees with their PIN. Owners and supervisors are Odoo users with
  VLUX roles; cashiers are employees who unlock the open register with a PIN.

The PINs come from the JSON file, which must not be committed or shared.

Example store.json:

    {
      "company": {"name": "Abarrotes Ejemplo", "vat": "XAXX010101000",
                  "fiscal_regime": "626 - Régimen Simplificado de Confianza",
                  "receipt_legend": "Gracias por su compra", "street": "Av. Juárez 10",
                  "city": "Toluca", "zip": "50000", "state_code": "MEX",
                  "phone": "722 000 0000", "email": "tienda@example.com"},
      "timezone": "America/Mexico_City",
      "prices_include_tax": true,
      "register": {"name": "Caja 1", "difference_limit": 30},
      "cashiers": [{"name": "Ana López", "pin": "4821"}]
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT = r'''
import json
STORE = json.loads(%(payload)r)
env = env(context=dict(env.context, lang="es_MX", tz=STORE.get("timezone") or "America/Mexico_City"))
report = []

# --- language and users -----------------------------------------------------
lang = env["res.lang"].with_context(active_test=False).search([("code", "=", "es_MX")], limit=1)
if not lang.active:
    env["base.language.install"].create({"lang_ids": [(6, 0, lang.ids)], "overwrite": False}).lang_install()
    report.append("idioma es_MX instalado")
company = env.company
tz = STORE.get("timezone") or "America/Mexico_City"
users = env["res.users"].search([("share", "=", False), ("company_ids", "in", company.id), ("id", "!=", env.ref("base.user_root").id)])
users.write({"lang": "es_MX", "tz": tz})
report.append("%%d usuario(s) en es_MX, %%s" %% (len(users), tz))

# --- company fiscal identity ---------------------------------------------------
data = STORE.get("company") or {}
values = {}
for key, field in (("name", "name"), ("vat", "vat"), ("street", "street"), ("city", "city"),
                   ("zip", "zip"), ("phone", "phone"), ("email", "email"),
                   ("fiscal_regime", "vlux_fiscal_regime"), ("receipt_legend", "vlux_receipt_legend")):
    if data.get(key):
        values[field] = data[key]
if data.get("state_code"):
    state = env["res.country.state"].search([("country_id.code", "=", "MX"), ("code", "=", data["state_code"])], limit=1)
    if state:
        values["state_id"] = state.id
if values:
    company.write(values)
    report.append("empresa: " + ", ".join(sorted(values)))

# --- shelf prices include tax --------------------------------------------------
# Odoo only lets this change before the company's first accounting entry, so
# it has to be right before the first sale.
price_include = "tax_included" if STORE.get("prices_include_tax", True) else "tax_excluded"
if company.account_price_include != price_include:
    if company.sudo()._existing_accounting():
        raise SystemExit("La empresa ya tiene ventas/asientos: no se puede cambiar si el precio incluye IVA.")
    company.account_price_include = price_include
    report.append("precios " + ("con IVA incluido" if price_include == "tax_included" else "sin IVA"))

# --- register ------------------------------------------------------------------
register = STORE.get("register") or {}
name = register.get("name") or "Caja 1"
Config = env["pos.config"]
config = Config.search([("name", "=", name), ("company_id", "=", company.id)], limit=1)
if not config:
    unused = Config.search([("company_id", "=", company.id), ("session_ids", "=", False)], limit=1)
    config = unused or Config.create({"name": name, "company_id": company.id})
    report.append("caja creada" if not unused else "caja existente reutilizada")
methods = config.payment_method_ids
cash = methods.filtered("is_cash_count")[:1]
card = methods.filtered(lambda method: method.journal_id.type == "bank")[:1]
if not cash or not card:
    raise SystemExit("La caja no tiene efectivo y tarjeta; revisar el plan contable de la empresa.")
cash.name = "Efectivo"
card.name = "Tarjeta"
limit = float(register.get("difference_limit", 30))
config.write({
    "name": name,
    "module_pos_hr": True,
    "iface_tax_included": "total",
    "set_maximum_difference": True,
    "amount_authorized_diff": limit,
    "payment_method_ids": [(6, 0, (cash | card).ids)],
})
report.append("caja %%s: Efectivo + Tarjeta, inicio por empleado, límite de arqueo $%%.2f" %% (name, limit))

# --- cashiers ------------------------------------------------------------------
Employee = env["hr.employee"].sudo()
for cashier in STORE.get("cashiers") or []:
    employee = Employee.search([("name", "=", cashier["name"]), ("company_id", "=", company.id)], limit=1)
    vals = {"name": cashier["name"], "company_id": company.id}
    if cashier.get("pin"):
        vals["pin"] = str(cashier["pin"])
    if employee:
        employee.write(vals)
    else:
        employee = Employee.create(vals)
    report.append("cajero: %%s (PIN %%s)" %% (employee.name, "sí" if employee.pin else "no"))

env.cr.commit()
print("VLUX_STORE_READY")
for line in report:
    print(" - " + line)
'''


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    store = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(SCRIPT % {"payload": json.dumps(store, ensure_ascii=False)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
