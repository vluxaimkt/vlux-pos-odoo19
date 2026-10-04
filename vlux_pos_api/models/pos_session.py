from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _vlux_api_closing_extra(self):
        """More sections for GET /registers/<id>/session/closing (hook; none by default)."""
        self.ensure_one()
        return {}
