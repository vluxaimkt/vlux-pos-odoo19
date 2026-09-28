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
* One register (``register.name``) with cash, card and, when vlux_pos_credit is
  installed, "Crédito" (sales on credit), employee login with
  PIN, prices shown with tax, and the closing difference limit
  (``register.difference_limit``, enforced by the server since vlux_core
  19.0.1.5.0).
* Cashier employees with their PIN, and managers (``managers``: the store's
  encargado) who get manager access to the register: they close it above the
  difference limit, move cash and reach the backend. The owner's employee (the
  one linked to the Owner user) gets ``owner_pin``.
* Every employee who can unlock the register must have a PIN: Odoo lets anyone
  at the register pick an employee without one, so a manager without a PIN is
  a manager for whoever is standing there. The script stops without saving
  anything if one is missing.
* Receipt: the company logo (``logo``, a PNG/JPG path relative to the JSON;
  without it the ticket prints the store name), the warehouse named after the
  store, and the "Need an invoice?" QR off unless ``invoicing`` is true.

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
      "owner_pin": "7390",
      "managers": [{"name": "Luis Pérez", "pin": "5162"}],
      "cashiers": [{"name": "Ana López", "pin": "4821"}],
      "logo": "logo.png",
      "invoicing": false
    }
"""
from __future__ import annotations

import base64
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

# --- receipt: logo, invoice QR, warehouse name --------------------------------------
if STORE.get("logo_b64"):
    company.logo = STORE["logo_b64"]
    report.append("logo cargado")
invoicing = bool(STORE.get("invoicing"))
if company.point_of_sale_use_ticket_qr_code != invoicing:
    company.point_of_sale_use_ticket_qr_code = invoicing
    report.append("ticket " + ("con" if invoicing else "sin") + " código para facturar")
warehouse = env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
if warehouse and company.name and warehouse.name != company.name:
    warehouse.name = company.name
    report.append("almacén: " + company.name)

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
# Write only what differs: Odoo refuses to touch payment methods (and some
# register settings) while a session is open, even to write the same value.
for method, label in ((cash, "Efectivo"), (card, "Tarjeta")):
    if method.name != label:
        method.name = label
limit = float(register.get("difference_limit", 30))
wanted = {
    "name": name,
    "module_pos_hr": True,
    "iface_tax_included": "total",
    "set_maximum_difference": True,
    "amount_authorized_diff": limit,
}
changes = {field: value for field, value in wanted.items() if config[field] != value}
# Sales on credit (vlux_pos_credit): Odoo's customer account, which asks for
# the customer. Only managers see it at the register.
methods = cash | card
if hasattr(env["pos.payment.method"], "_vlux_credit_method"):
    methods |= env["pos.payment.method"]._vlux_credit_method(company)
if config.payment_method_ids != methods:
    changes["payment_method_ids"] = [(6, 0, methods.ids)]
if changes:
    config.write(changes)
report.append("caja %%s: %%s, inicio por empleado, límite de arqueo $%%.2f" %% (name, " + ".join(methods.mapped("name")), limit))

# --- people ------------------------------------------------------------------------
Employee = env["hr.employee"].sudo()

def upsert(person):
    employee = Employee.search([("name", "=", person["name"]), ("company_id", "=", company.id)], limit=1)
    vals = {"name": person["name"], "company_id": company.id}
    if person.get("pin"):
        vals["pin"] = str(person["pin"])
    if employee:
        employee.write(vals)
    else:
        employee = Employee.create(vals)
    return employee

for cashier in STORE.get("cashiers") or []:
    employee = upsert(cashier)
    report.append("cajero: %%s (PIN %%s)" %% (employee.name, "sí" if employee.pin else "no"))
managers = Employee.browse()
for manager in STORE.get("managers") or []:
    managers |= upsert(manager)
    report.append("encargado: %%s (PIN %%s)" %% (managers[-1:].name, "sí" if managers[-1:].pin else "no"))
if managers:
    config.sudo().write({"advanced_employee_ids": [(4, employee.id) for employee in managers]})

# Employees linked to POS-manager users (the Owner) are managers at the register.
manager_group = env.ref("point_of_sale.group_pos_manager")
owners = Employee.search([("company_id", "=", company.id), ("user_id", "!=", False)]).filtered(
    lambda employee: manager_group in employee.user_id.all_group_ids)
if STORE.get("owner_pin"):
    owners.write({"pin": str(STORE["owner_pin"])})
    report.append("dueño: %%s (PIN sí)" %% ", ".join(owners.mapped("name")))

# Nobody may unlock the register without a PIN (see the module docstring).
at_register = Employee.search([("company_id", "=", company.id)])
if config.basic_employee_ids or config.advanced_employee_ids or config.minimal_employee_ids:
    at_register = config.basic_employee_ids | config.advanced_employee_ids | config.minimal_employee_ids | owners
without_pin = at_register.filtered(lambda employee: not employee.pin)
if without_pin:
    raise SystemExit("Sin guardar nada: estas personas podrían entrar a la caja sin NIP: %%s. "
                     "Agrega su NIP (owner_pin para el dueño, managers/cashiers para los demás)."
                     %% ", ".join(without_pin.mapped("name")))
report.append("todos los que entran a la caja tienen NIP")

env.cr.commit()
print("VLUX_STORE_READY")
for line in report:
    print(" - " + line)
'''


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    source = Path(sys.argv[1])
    store = json.loads(source.read_text(encoding="utf-8"))
    if store.get("logo"):
        store["logo_b64"] = base64.b64encode((source.parent / store["logo"]).read_bytes()).decode()
    # The shell reads UTF-8; a Windows console would otherwise write cp1252.
    sys.stdout.reconfigure(encoding="utf-8")
    print(SCRIPT % {"payload": json.dumps(store, ensure_ascii=False)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
