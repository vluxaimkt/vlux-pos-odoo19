from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_vlux_credit_sellers = fields.Selection(related="pos_config_id.vlux_credit_sellers", readonly=False)
