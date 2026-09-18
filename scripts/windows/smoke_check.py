import os

mods = ["vlux_core", "vlux_mobile_scanner", "vlux_owner", "vlux_pos_catalog"]
profile = (os.environ.get("VLUX_PROFILE") or "local_complete").lower()
if profile == "local_core":
    mods = ["vlux_core"]
elif profile == "scanner":
    mods = ["vlux_mobile_scanner"]
elif profile == "owner":
    mods = ["vlux_owner"]
elif profile == "catalog":
    mods = ["vlux_pos_catalog"]
elif profile == "facturacion_internal":
    mods = ["vlux_facturacion"]
elif profile == "custom":
    mods = [m.strip() for m in (os.environ.get("VLUX_MODULES") or "").split(",") if m.strip()]

installed = env["ir.module.module"].sudo().search([("name", "in", mods)])
states = {module.name: module.state for module in installed}
missing = [module for module in mods if states.get(module) != "installed"]
if missing:
    raise SystemExit("VLUX modules not installed: %s" % ", ".join(missing))

env.cr.execute("select 1")
print("Smoke OK: %s" % ", ".join(mods))
