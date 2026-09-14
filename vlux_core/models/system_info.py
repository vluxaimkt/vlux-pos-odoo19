from odoo import api, release, models, _
from odoo.exceptions import AccessError


VLUX_CORE_VERSION = "19.0.1.0.0"
VLUX_ADDON_PREFIX = "vlux_"
VALID_EDITIONS = {"local_core", "local_complete", "cloud_managed"}


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
        }

    @api.model
    def health_status(self):
        return {"status": "ok"}
