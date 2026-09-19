import re
import uuid
from datetime import timedelta

from odoo import api, fields, models


EVENT_RETENTION_DAYS = 30
RESULT_STATES = ("delivered", "not_found", "failed")
CLIENT_REQUEST_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
PUSH_NOTIFICATION_TYPE = "VLUX_MOBILE_RESULT"


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

    # ------------------------------------------------------------------
    # idempotent intake
    # ------------------------------------------------------------------

    @api.model
    def _clean_client_request_id(self, value):
        """Client supplied request ids must be UUIDs; anything else is ignored."""
        value = str(value or "").strip().lower()
        return value if CLIENT_REQUEST_ID_RE.match(value) else ""

    @api.model
    def register_scan(self, pairing, barcode, client_request_id=None):
        """Create the queued event for a scan, or return the existing one.

        A phone that retries the same scan after a network hiccup sends the
        same request_id; the second delivery must not create a second event
        nor a second POS notification. Returns ``(event, created)``.
        """
        request_id = self._clean_client_request_id(client_request_id)
        if request_id:
            existing = self.sudo().search([("request_id", "=", request_id)], limit=1)
            if existing:
                if existing.pairing_id != pairing:
                    return self.browse(), False
                return existing, False
        else:
            request_id = str(uuid.uuid4())
        event = self.sudo().create({
            "request_id": request_id,
            "pairing_id": pairing.id,
            "barcode": barcode,
            "device_identifier": pairing.device_identifier,
        })
        return event, True

    def notify_pos(self):
        """Hand the barcode to the POS through the native pos.config bus channel."""
        for event in self:
            pairing = event.pairing_id
            pairing.pos_config_id.sudo()._notify(
                "VLUX_MOBILE_BARCODE",
                {
                    "request_id": event.request_id,
                    "event_id": event.id,
                    "pairing_id": pairing.id,
                    "pos_config_id": pairing.pos_config_id.id,
                    "pos_session_id": pairing.pos_session_id.id,
                    "device_identifier": pairing.device_identifier,
                    "barcode": event.barcode,
                },
            )

    # ------------------------------------------------------------------
    # result delivery
    # ------------------------------------------------------------------

    def result_payload(self):
        self.ensure_one()
        product = None
        if self.product_id or self.product_name:
            product = {
                "id": self.product_id.id,
                "name": self.product_name or self.product_id.display_name,
                "unit_price": self.unit_price,
            }
        return {
            "request_id": self.request_id,
            "barcode": self.barcode,
            "status": self.state,
            "result_code": self.result_code or "",
            "message": self.result_message or "",
            "product": product,
        }

    def apply_pos_result(self, values):
        """Record the POS outcome and push it to the phone.

        ``values`` may contain state, result_code, result_message, product_id,
        product_name and unit_price. The push goes through the Odoo bus to the
        pairing's private channel; phones without a websocket fall back to the
        batch results endpoint, which reads the same stored state.
        """
        self.ensure_one()
        state = values.get("state") if values.get("state") in RESULT_STATES else "failed"
        self.sudo().write({
            "state": state,
            "result_code": str(values.get("result_code") or "")[:128],
            "result_message": str(values.get("result_message") or "")[:500],
            "product_id": values.get("product_id") or False,
            "product_name": str(values.get("product_name") or "")[:255],
            "unit_price": float(values.get("unit_price") or 0.0),
            "processed_at": fields.Datetime.now(),
        })
        self.push_result()
        return True

    def push_result(self):
        channel = self.pairing_id.push_channel
        if not channel:
            return False
        self.env["bus.bus"].sudo()._sendone(
            channel,
            PUSH_NOTIFICATION_TYPE,
            {"results": [self.result_payload()]},
        )
        return True

    @api.model
    def results_for(self, pairing, request_ids):
        """Batch lookup used by the polling fallback: one query for many ids."""
        events = self.sudo().search([
            ("pairing_id", "=", pairing.id),
            ("request_id", "in", list(request_ids)),
        ])
        by_id = {event.request_id: event for event in events}
        rows = []
        for request_id in request_ids:
            event = by_id.get(request_id)
            if event:
                rows.append(event.result_payload())
            else:
                rows.append({"request_id": request_id, "status": "unknown"})
        return rows

    @api.autovacuum
    def _gc_old_events(self):
        cutoff = fields.Datetime.now() - timedelta(days=EVENT_RETENTION_DAYS)
        self.sudo().search([("create_date", "<", cutoff)]).unlink()
