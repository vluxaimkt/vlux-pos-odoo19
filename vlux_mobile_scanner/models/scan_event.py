from datetime import timedelta

from odoo import api, fields, models


EVENT_RETENTION_DAYS = 30


class VluxMobileScannerEvent(models.Model):
    _name = "vlux.mobile.scanner.event"
    _description = "VLUX Mobile Scanner Event"
    _order = "create_date desc"

    request_id = fields.Char(required=True, index=True, readonly=True)
    pairing_id = fields.Many2one("vlux.mobile.scanner.pairing", required=True, index=True, ondelete="cascade")
    pos_config_id = fields.Many2one("pos.config", related="pairing_id.pos_config_id", store=True, index=True)
    pos_session_id = fields.Many2one("pos.session", related="pairing_id.pos_session_id", store=True, index=True)
    company_id = fields.Many2one("res.company", related="pairing_id.company_id", store=True, index=True)
    barcode = fields.Char(required=True, index=True)
    state = fields.Selection(
        [
            ("queued", "Enviado al POS"),
            ("delivered", "Agregado al carrito"),
            ("not_found", "No encontrado"),
            ("failed", "Error"),
        ],
        default="queued",
        required=True,
        index=True,
    )
    result_code = fields.Char(index=True)
    result_message = fields.Char()
    product_id = fields.Many2one("product.product", ondelete="set null")
    product_name = fields.Char()
    unit_price = fields.Float()
    processed_at = fields.Datetime()
    device_identifier = fields.Char(index=True)

    _request_id_unique = models.Constraint(
        "unique (request_id)",
        "El identificador de solicitud debe ser unico.",
    )

    @api.autovacuum
    def _gc_old_events(self):
        cutoff = fields.Datetime.now() - timedelta(days=EVENT_RETENTION_DAYS)
        self.sudo().search([("create_date", "<", cutoff)]).unlink()
