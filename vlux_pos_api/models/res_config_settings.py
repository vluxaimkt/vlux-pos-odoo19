from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_vlux_cashier_cash_out = fields.Boolean(related="pos_config_id.vlux_cashier_cash_out", readonly=False)
