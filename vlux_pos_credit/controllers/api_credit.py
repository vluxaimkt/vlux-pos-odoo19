"""VLUX API v1: credit (fiado) for the register PWA.

Same rules as the Odoo POS (decision D7): any cashier sees balances and
receives abonos; only the encargado or the owner authorises a customer and
sets the limit. Writing goes through the module's own methods
(``vlux_pos_set_credit``, ``vlux_pos_register_abono``), so both registers
behave identically.
"""
from odoo import http
from odoo.http import request

from odoo.addons.vlux_core.controllers.api import VluxApiError, api_route, json_body
from odoo.addons.vlux_pos_api.controllers.api_sales import _acting_employee, _register, _register_from_body


def _partner(partner_id):
    partner = request.env["res.partner"].search([("id", "=", partner_id)], limit=1)
    if not partner:
        raise VluxApiError("NOT_FOUND", "El cliente no existe.")
    return partner


def _text(value, name, limit, required=False):
    value = (value or "").strip() if isinstance(value, str) else value
    if not value:
        if required:
            raise VluxApiError("VALIDATION_ERROR", "Falta %s." % name)
        return False
    if not isinstance(value, str) or len(value) > limit:
        raise VluxApiError("VALIDATION_ERROR", "%s debe ser un texto de hasta %d caracteres." % (name, limit))
    return value


class VluxApiCredit(http.Controller):

    @api_route("/credit/customers", scope="catalog:read", summary="Clientes con crédito o que deben, con límite y saldo")
    def credit_customers(self, token, **kwargs):
        """Customers authorised for credit or with a balance: limit, balance, what is left."""
        return {"items": request.env["res.partner"]._vlux_api_credit_rows()}

    @api_route("/credit/customers/<int:partner_id>", scope="catalog:read", summary="Crédito de un cliente, al momento")
    def credit_customer(self, token, partner_id, **kwargs):
        """One customer's credit as the server knows it now (before a sale on credit or an abono)."""
        return _partner(partner_id)._vlux_api_credit_row()

    @api_route("/credit/customers/<int:partner_id>", scope="orders:write", methods=("POST",),
               summary="Autorizar o retirar el crédito de un cliente y fijar su límite")
    def set_credit(self, token, partner_id, **kwargs):
        """Body ``{"register_id", "allowed", "limit", "employee_id"?}``.

        Only the encargado or the owner (``FORBIDDEN`` otherwise). ``limit`` 0
        means no limit. The change is logged on the customer.
        """
        body = json_body()
        config = _register_from_body(body, token)
        employee, _verified = _acting_employee(config, token, body, required=True)
        partner = _partner(partner_id)
        request.env["res.partner"].vlux_pos_set_credit(
            partner.id, bool(body.get("allowed")), body.get("limit") or 0.0, config.id, employee.id or False,
        )
        return partner._vlux_api_credit_row()

    @api_route("/credit/abonos", scope="orders:write", methods=("POST",),
               summary="Registrar un abono de un cliente (idempotente por uuid)")
    def register_abono(self, token, **kwargs):
        """Body ``{"uuid", "register_id", "partner_id", "amount", "payment_method_id", "employee_id"?}``.

        Any cashier. Cash or card (not Crédito); never more than what the
        customer owes. Lands in the open session, so cash enters the closing.
        Sending the same ``uuid`` again returns the same abono.
        """
        body = json_body()
        config = _register_from_body(body, token)
        session = config.current_session_id
        if not session or session.state != "opened":
            raise VluxApiError("NO_OPEN_SESSION", "Abre la caja para recibir abonos.")
        employee, _verified = _acting_employee(config, token, body, required=True)
        try:
            partner_id = int(body.get("partner_id"))
            method_id = int(body.get("payment_method_id"))
        except (TypeError, ValueError):
            raise VluxApiError("VALIDATION_ERROR", "partner_id y payment_method_id deben ser enteros.")
        partner = _partner(partner_id)
        body["payment_method_id"] = method_id
        ticket = request.env["pos.session"].vlux_pos_register_abono(
            session.id, partner.id, body.get("amount"), body.get("payment_method_id"),
            body.get("uuid"), employee.id or False,
        )
        return {**ticket, "customer": partner._vlux_api_credit_row()}

    @api_route("/customers", scope="orders:write", methods=("POST",), summary="Dar de alta un cliente desde la caja")
    def create_customer(self, token, **kwargs):
        """Body ``{"name", "phone"?, "email"?, "vat"?}``.

        Created as the token's user: Odoo's own permission (contact creation)
        decides, as on the Odoo POS. Answers the customer as the feed does.
        """
        body = json_body()
        values = {
            "name": _text(body.get("name"), "name", 128, required=True),
            "phone": _text(body.get("phone"), "phone", 32),
            "email": _text(body.get("email"), "email", 128),
            "vat": _text(body.get("vat"), "vat", 20),
            "company_id": request.env.company.id,
        }
        partner = request.env["res.partner"].create({key: value for key, value in values.items() if value})
        return {"id": partner.id, "name": partner.name, "phone": partner.phone or None,
                "email": partner.email or None, "vat": partner.vat or None}
