import logging

from odoo import api, release, models, _
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)


VLUX_CORE_VERSION = "19.0.1.1.0"
VLUX_ADDON_PREFIX = "vlux_"
VALID_EDITIONS = {"local_core", "local_complete", "cloud_managed"}
# Addons que cada edición debe tener instalados para considerarse lista. Debe
# coincidir con PRODUCTIVE_EDITIONS de scripts/distribution/vlux_pos.py y
# PRODUCTIVE_ADDONS de packaging/cloud/vlux_cloud.py.
EDITION_REQUIRED_ADDONS = {
    "local_core": ("vlux_core",),
    "local_complete": ("vlux_core", "vlux_mobile_scanner", "vlux_owner"),
    "cloud_managed": ("vlux_core", "vlux_mobile_scanner", "vlux_owner"),
}
PENDING_MODULE_STATES = ("to install", "to upgrade", "to remove")


class VluxCoreSystemInfo(models.AbstractModel):
    _name = "vlux.core.system.info"
    _description = "VLUX Core System Info"

    @api.model
    def _edition(self):
        value = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("vlux_core.edition", "local_core")
        )
        return value if value in VALID_EDITIONS else "local_core"

    @api.model
    def _release_version(self):
        return (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("vlux_core.release_version", "")
        )

    @api.model
    def _installed_vlux_addons(self):
        modules = self.env["ir.module.module"].sudo().search(
            [
                ("name", "=like", f"{VLUX_ADDON_PREFIX}%"),
                ("state", "=", "installed"),
            ],
            order="name",
        )
        return [
            {
                "name": module.name,
                "version": module.installed_version or module.latest_version or "",
                "state": module.state,
            }
            for module in modules
        ]

    @api.model
    def get_safe_info(self):
        if not self.env.user.has_group("vlux_core.group_vlux_support"):
            raise AccessError(_("Solo VLUX Support puede consultar system info."))
        return {
            "odoo_version": release.version,
            "vlux_core_version": VLUX_CORE_VERSION,
            "edition": self._edition(),
            "release_version": self._release_version(),
            "company": {
                "id": self.env.company.id,
                "name": self.env.company.display_name,
            },
            "addons": self._installed_vlux_addons(),
            "ready": self.readiness(),
        }

    @api.model
    def health_status(self):
        return {"status": "ok"}

    @api.model
    def readiness(self):
        """Estado de preparación para recibir tráfico (``/vlux/ready``).

        A diferencia de ``/vlux/health`` (el proceso responde), comprueba que la
        base sea utilizable para vender. Sólo devuelve códigos estables, sin
        nombres de módulos, rutas ni datos del negocio, porque la ruta es pública.
        """
        checks = {}
        try:
            self.env.cr.execute("SELECT 1")
            checks["database"] = "ok" if self.env.cr.fetchone() == (1,) else "error"
        except Exception:
            _logger.exception("VLUX readiness: la base de datos no responde")
            return {"status": "not_ready", "checks": {"database": "error"}}

        Module = self.env["ir.module.module"].sudo()
        required = EDITION_REQUIRED_ADDONS[self._edition()]
        installed = set(
            Module.search([("name", "in", list(required)), ("state", "=", "installed")]).mapped("name")
        )
        checks["addons"] = "ok" if installed >= set(required) else "missing"
        pending = Module.search_count([("state", "in", PENDING_MODULE_STATES)])
        checks["module_updates"] = "ok" if not pending else "pending"
        has_register = self.env["pos.config"].sudo().search_count([("active", "=", True)], limit=1)
        checks["pos_config"] = "ok" if has_register else "missing"

        ready = all(value == "ok" for value in checks.values())
        return {"status": "ready" if ready else "not_ready", "checks": checks}
