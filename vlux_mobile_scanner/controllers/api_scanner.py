"""VLUX API v1: the phone scanner for registers that run without an Odoo session (the PWA).

The phone side is unchanged (``/vlux/scanner``, ``/vlux/mobile/*``). What
changes is how the register hears about a scan: the Odoo POS listens on the
Odoo bus with its session; a register that holds only an API token asks for
the scans waiting for it (``GET .../scanner/events``) while a phone is
linked, and answers each one (``POST .../scanner/ack``) so the phone shows
whether it went into the cart.

A pairing made here belongs to one register and one device of that register
(``device_id`` chosen by the device), exactly as in the Odoo POS.
"""
import re
from datetime import timedelta

from odoo import fields, http
from odoo.http import request

from odoo.addons.vlux_core.controllers.api import VluxApiError, api_route, json_body

from ..models.scan_event import RESULT_STATES
from .main import VluxMobileScannerController

DEVICE_PREFIX = "api:"
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
# A scan the register did not pick up within this time is answered as lost:
# adding it minutes later, when nobody expects it, would surprise the cashier.
EVENT_MAX_AGE_SECONDS = 120
EVENTS_BATCH = 20


def _register(register_id, token):
    config = request.env["pos.config"].search(
        [("id", "=", register_id), ("company_id", "=", request.env.company.id)], limit=1,
    )
    if not config:
        raise VluxApiError("NOT_FOUND", "La caja no existe.")
    if not token.allows_register(config):
        raise VluxApiError("FORBIDDEN", "Este token es de otra caja.")
    return config


def _device(value):
    value = str(value or "").strip()
    if not DEVICE_ID_RE.match(value):
        raise VluxApiError("VALIDATION_ERROR", "device_id inválido.")
    return DEVICE_PREFIX + value


def _pairing(config, pairing_id, device):
    try:
        pairing_id = int(pairing_id)
    except (TypeError, ValueError):
        raise VluxApiError("VALIDATION_ERROR", "pairing_id debe ser un entero.")
    pairing = request.env["vlux.mobile.scanner.pairing"].sudo().search(
        [("id", "=", pairing_id), ("pos_config_id", "=", config.id), ("device_identifier", "=", device)], limit=1,
    )
    if not pairing:
        raise VluxApiError("NOT_FOUND", "La vinculación no existe en este equipo.")
    return pairing


class VluxApiMobileScanner(http.Controller):

    @api_route("/registers/<int:register_id>/scanner/pair", scope="orders:write", methods=("POST",),
               summary="Vincular un celular como escáner de esta caja (código QR)")
    def pair(self, token, register_id, **kwargs):
        """Body ``{"device_id"}``: a new pairing code and its QR for the open session."""
        config = _register(register_id, token)
        body = json_body()
        device = _device(body.get("device_id"))
        session = config.current_session_id
        if not session or session.state not in ("opening_control", "opened"):
            raise VluxApiError("NO_OPEN_SESSION", "Abre la caja para vincular un celular.")
        pairing = request.env["vlux.mobile.scanner.pairing"].create_waiting_pairing(session, device)
        helper = VluxMobileScannerController()
        url = helper._scanner_url(pairing.pair_code)
        return {
            "pairing_id": pairing.id,
            "code": pairing.pair_code,
            "status": pairing.state,
            "expires_at": fields.Datetime.to_string(pairing.pair_expires_at) + "Z",
            "scanner_url": url,
            "qr_data_uri": helper._qr_data_uri(url),
        }

    @api_route("/registers/<int:register_id>/scanner/status", scope="orders:write",
               summary="Estado de la vinculación del celular",
               params=[{"name": "pairing_id", "in": "query", "schema": {"type": "integer"}},
                       {"name": "device_id", "in": "query", "schema": {"type": "string"}}])
    def status(self, token, register_id, pairing_id=None, device_id=None, **kwargs):
        config = _register(register_id, token)
        pairing = _pairing(config, pairing_id, _device(device_id))
        pairing.refresh_state()
        return {
            "pairing_id": pairing.id,
            "status": pairing.state,
            "last_seen_at": fields.Datetime.to_string(pairing.last_seen_at) + "Z" if pairing.last_seen_at else None,
        }

    @api_route("/registers/<int:register_id>/scanner/revoke", scope="orders:write", methods=("POST",),
               summary="Desvincular el celular")
    def revoke(self, token, register_id, **kwargs):
        config = _register(register_id, token)
        body = json_body()
        pairing = _pairing(config, body.get("pairing_id"), _device(body.get("device_id")))
        pairing.action_revoke()
        return {"pairing_id": pairing.id, "status": pairing.state}

    @api_route("/registers/<int:register_id>/scanner/events", scope="orders:write",
               summary="Lecturas del celular que esperan a esta caja",
               params=[{"name": "pairing_id", "in": "query", "schema": {"type": "integer"}},
                       {"name": "device_id", "in": "query", "schema": {"type": "string"}}])
    def events(self, token, register_id, pairing_id=None, device_id=None, **kwargs):
        """Scans still queued for the pairing, oldest first; stale ones are answered as lost."""
        config = _register(register_id, token)
        pairing = _pairing(config, pairing_id, _device(device_id))
        pairing.refresh_state()
        Event = request.env["vlux.mobile.scanner.event"].sudo()
        queued = Event.search([("pairing_id", "=", pairing.id), ("state", "=", "queued")], order="id", limit=100)
        cutoff = fields.Datetime.now() - timedelta(seconds=EVENT_MAX_AGE_SECONDS)
        for stale in queued.filtered(lambda event: event.create_date < cutoff):
            stale.apply_pos_result({"state": "failed", "result_code": "EXPIRED",
                                    "result_message": "La caja no recibió la lectura a tiempo; escanea otra vez."})
        fresh = queued.filtered(lambda event: event.state == "queued")[:EVENTS_BATCH]
        return {
            "status": pairing.state,
            "items": [{"request_id": event.request_id, "barcode": event.barcode} for event in fresh],
        }

    @api_route("/registers/<int:register_id>/scanner/ack", scope="orders:write", methods=("POST",),
               summary="Resultado de una lectura (se muestra en el celular)")
    def ack(self, token, register_id, **kwargs):
        """Body ``{"device_id", "request_id", "status", "result_code"?, "message"?, "product_id"?, "product_name"?, "unit_price"?}``."""
        config = _register(register_id, token)
        body = json_body()
        device = _device(body.get("device_id"))
        event = request.env["vlux.mobile.scanner.event"].sudo().search(
            [("request_id", "=", str(body.get("request_id") or "")), ("pos_config_id", "=", config.id),
             ("device_identifier", "=", device)], limit=1,
        )
        if not event:
            raise VluxApiError("NOT_FOUND", "La lectura no existe en este equipo.")
        status = body.get("status") if body.get("status") in RESULT_STATES else "failed"
        product = request.env["product.product"]
        try:
            if body.get("product_id"):
                product = product.search([("id", "=", int(body["product_id"]))], limit=1)
            unit_price = float(body.get("unit_price") or 0.0)
        except (TypeError, ValueError):
            raise VluxApiError("VALIDATION_ERROR", "product_id y unit_price deben ser números.")
        event.apply_pos_result({
            "state": status,
            "result_code": body.get("result_code"),
            "result_message": body.get("message"),
            "product_id": product.id or False,
            "product_name": body.get("product_name") or product.display_name or "",
            "unit_price": unit_price,
        })
        return {"request_id": event.request_id, "status": event.state}
