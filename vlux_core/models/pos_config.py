from odoo import api, models, _
from odoo.exceptions import AccessError


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _vlux_is_cashier_only(self):
        user = self.env.user
        return user.has_group("vlux_core.group_vlux_cashier") and not (
            user.has_group("vlux_core.group_vlux_supervisor")
            or user.has_group("vlux_core.group_vlux_administrator")
            or user.has_group("vlux_core.group_vlux_owner")
        )

    def _vlux_guard_sensitive_pos_config(self):
        if self._vlux_is_cashier_only():
            raise AccessError(
                _(
                    "VLUX Cashier puede operar el POS, pero no administrar "
                    "configuración de cajas."
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        self._vlux_guard_sensitive_pos_config()
        return super().create(vals_list)

    def write(self, vals):
        self._vlux_guard_sensitive_pos_config()
        return super().write(vals)

    def unlink(self):
        self._vlux_guard_sensitive_pos_config()
        return super().unlink()
