"""A register asks the linked phone for a product picture.

The register (Odoo POS or VLUX PWA) is often a PC without a camera; the phone
already linked as its scanner has one. The register creates a request, the
phone is told (bus push, and the heartbeat as a fallback), takes the picture
and uploads it, and the register picks it up for the product form.
"""
import base64
import binascii
import uuid
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

PHOTO_REQUEST_TYPE = "VLUX_MOBILE_PHOTO_REQUEST"
PHOTO_TTL_MINUTES = 10
PHOTO_RETENTION_HOURS = 24
# The phone downsizes to ~1024 px JPEG first; this only stops abuse.
PHOTO_MAX_BYTES = 6 * 1024 * 1024


class VluxMobileScannerPhoto(models.Model):
    _name = "vlux.mobile.scanner.photo"
    _description = "VLUX Mobile Scanner photo request"
    _order = "id desc"

    request_id = fields.Char(required=True, index=True, readonly=True)
    pairing_id = fields.Many2one("vlux.mobile.scanner.pairing", required=True, index=True, ondelete="cascade")
    pos_config_id = fields.Many2one("pos.config", related="pairing_id.pos_config_id", store=True, index=True)
    barcode = fields.Char()
    label = fields.Char()
    state = fields.Selection(
        [("requested", "Pedida"), ("uploaded", "Recibida"), ("cancelled", "Cancelada")],
        default="requested", required=True, index=True,
    )
    expires_at = fields.Datetime(required=True)
    image = fields.Binary(attachment=True)

    _request_id_unique = models.Constraint("unique (request_id)", "El pedido de foto debe ser único.")

    @api.model
    def create_request(self, pairing, barcode="", label=""):
        """Ask the phone of ``pairing`` for a picture; an older open request of the pairing is cancelled."""
        self.sudo().search([("pairing_id", "=", pairing.id), ("state", "=", "requested")]).write({"state": "cancelled"})
        request = self.sudo().create({
            "request_id": str(uuid.uuid4()),
            "pairing_id": pairing.id,
            "barcode": str(barcode or "")[:128],
            "label": str(label or "")[:120],
            "expires_at": fields.Datetime.now() + timedelta(minutes=PHOTO_TTL_MINUTES),
        })
        request._push()
        return request

    def _payload(self):
        self.ensure_one()
        return {"request_id": self.request_id, "barcode": self.barcode or "", "label": self.label or ""}

    def _push(self):
        for request in self:
            channel = request.pairing_id.push_channel
            if channel:
                self.env["bus.bus"].sudo()._sendone(channel, PHOTO_REQUEST_TYPE, request._payload())

    @api.model
    def pending_for(self, pairing):
        """The open request of the phone, if any (heartbeat fallback when the push did not arrive)."""
        return self.sudo().search([
            ("pairing_id", "=", pairing.id), ("state", "=", "requested"),
            ("expires_at", ">", fields.Datetime.now()),
        ], limit=1)

    def upload(self, value):
        """Store the picture sent by the phone (base64 or data URI), checked to be an image."""
        self.ensure_one()
        if self.state != "requested" or self.expires_at <= fields.Datetime.now():
            raise ValidationError(_("La caja ya no espera esta foto."))
        if not isinstance(value, str) or not value:
            raise ValidationError(_("La foto no es válida."))
        payload = value.split(",", 1)[1] if value.startswith("data:") else value
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise ValidationError(_("La foto no es válida."))
        if len(raw) > PHOTO_MAX_BYTES:
            raise ValidationError(_("La foto es demasiado grande."))
        try:
            from odoo.tools.image import ImageProcess

            ImageProcess(raw)
        except Exception:  # noqa: BLE001 - any decoding failure means "not an image"
            raise ValidationError(_("El archivo no es una imagen reconocida."))
        self.sudo().write({"image": base64.b64encode(raw), "state": "uploaded"})
        return True

    @api.autovacuum
    def _gc_photo_requests(self):
        cutoff = fields.Datetime.now() - timedelta(hours=PHOTO_RETENTION_HOURS)
        self.sudo().search([("create_date", "<", cutoff)]).unlink()
