from odoo import api, models


class AccountTax(models.Model):
    _inherit = "account.tax"

    @api.model
    def _load_pos_data_fields(self, config):
        # type_tax_use lets the quick-create form list only sale taxes.
        return super()._load_pos_data_fields(config) + ["type_tax_use"]
