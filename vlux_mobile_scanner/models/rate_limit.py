from datetime import timedelta

from odoo import api, fields, models


class VluxMobileScannerRateLimit(models.Model):
    """Request budget of the phone scanner.

    Counting is delegated to the shared ``vlux.rate.limit`` (its own short
    transaction survives concurrent bursts); this model only keeps its legacy
    table so existing installations upgrade without a migration.
    """

    _name = "vlux.mobile.scanner.rate.limit"
    _description = "Límite de solicitudes del escáner móvil"

    key = fields.Char(required=True, index=True, readonly=True)
    window_start = fields.Datetime(required=True, readonly=True)
    request_count = fields.Integer(required=True, readonly=True)

    _key_unique = models.Constraint(
        "UNIQUE (key)",
        "La clave del límite de solicitudes debe ser única.",
    )

    @api.model
    def consume(self, scope, identity, limit, window_seconds):
        return self.env["vlux.rate.limit"].sudo().consume(
            f"scanner:{scope}", identity, limit, window_seconds
        )

    @api.autovacuum
    def _gc_old_windows(self):
        cutoff = fields.Datetime.now() - timedelta(days=1)
        self.sudo().search([("window_start", "<", cutoff)]).unlink()
