from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    vlux_catalog_can_quick_create = fields.Boolean(
        compute="_compute_vlux_catalog_can_quick_create",
        compute_sudo=False,
    )

    @api.depends("group_ids")
    def _compute_vlux_catalog_can_quick_create(self):
        for user in self:
            user.vlux_catalog_can_quick_create = user.has_group(
                "vlux_pos_catalog.group_vlux_catalog_quick_create"
            )

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ["vlux_catalog_can_quick_create"]
