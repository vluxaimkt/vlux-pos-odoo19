from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_vlux_cashier_cash_out = fields.Boolean(related="pos_config_id.vlux_cashier_cash_out", readonly=False)
    pos_vlux_refunds_need_manager = fields.Boolean(related="pos_config_id.vlux_refunds_need_manager", readonly=False)
    pos_vlux_catalog_editors = fields.Selection(related="pos_config_id.vlux_catalog_editors", readonly=False)
    pos_vlux_staff_admins = fields.Selection(related="pos_config_id.vlux_staff_admins", readonly=False)
    pos_vlux_cash_in_reasons = fields.Text(related="pos_config_id.vlux_cash_in_reasons", readonly=False)
    pos_vlux_cash_out_reasons = fields.Text(related="pos_config_id.vlux_cash_out_reasons", readonly=False)
