"""VLUX API v1, phase C: running a register and selling.

A register (``pos.config``) is opened, sells and is closed through the same
Odoo methods the Odoo POS calls, so sessions, closings and accounting are
identical whichever screen is used. The rules that protect the drawer (the
closing difference limit, who may close above it) are enforced by those
server methods, not by the client.

Every write runs inside the savepoint ``api_route`` opens: a refused closing
or sale leaves nothing behind.
"""
from odoo import _, fields, http
from odoo.http import request

from odoo.addons.vlux_core.controllers.api import VluxApiError, api_route, json_body
from odoo.addons.vlux_pos_api.models.pos_order import VluxSaleRefused


def _register(register_id):
    """The register of the token's company, or NOT_FOUND."""
    config = request.env["pos.config"].search(
        [("id", "=", register_id), ("company_id", "=", request.env.company.id)], limit=1,
    )
    if not config:
        raise VluxApiError("NOT_FOUND", "La caja no existe.")
    return config


def _amount(value, name, required=True):
    if value in (None, ""):
        if required:
            raise VluxApiError("VALIDATION_ERROR", "Falta %s." % name)
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise VluxApiError("VALIDATION_ERROR", "%s debe ser un número." % name)
    if number < 0:
        raise VluxApiError("VALIDATION_ERROR", "%s no puede ser negativo." % name)
    return number


def _text(value, name, limit=1000):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise VluxApiError("VALIDATION_ERROR", "%s debe ser un texto de hasta %d caracteres." % (name, limit))
    return value


def _stamp(value):
    return fields.Datetime.to_string(value) + "Z" if value else None


def _session_payload(session):
    if not session:
        return None
    return {
        "id": session.id,
        "name": session.name,
        "state": session.state,
        "opened_at": _stamp(session.start_at),
        "closed_at": _stamp(session.stop_at),
        "opening_cash": session.cash_register_balance_start,
        "employee_id": session.employee_id.id or None,
        "user_id": session.user_id.id or None,
    }


def _register_state(config):
    session = config.current_session_id
    return {
        "register_id": config.id,
        "name": config.name,
        "employee_login": bool(config.module_pos_hr),
        "cash_control": bool(config.cash_control),
        "max_difference": config.amount_authorized_diff if config.set_maximum_difference else None,
        "session": _session_payload(session),
    }


def _quote_payload(quote):
    return {
        "pricelist_id": quote["pricelist"].id or None,
        "fiscal_position_id": quote["fiscal_position"].id or None,
        "amount_untaxed": quote["amount_untaxed"],
        "amount_tax": quote["amount_tax"],
        "amount_total": quote["amount_total"],
        "lines": [
            {
                "uuid": line["uuid"],
                "product_id": line["product"].id,
                "name": line["product"].display_name,
                "qty": line["qty"],
                "price_unit": line["price_unit"],
                "catalog_price": line["catalog_price"],
                "price_overridden": line["price_overridden"],
                "tax_ids": line["taxes"].ids,
                "price_subtotal": line["price_subtotal"],
                "price_subtotal_incl": line["price_subtotal_incl"],
            }
            for line in quote["lines"]
        ],
    }


def _partner(partner_id):
    if not partner_id:
        return request.env["res.partner"]
    partner = request.env["res.partner"].search([("id", "=", partner_id)], limit=1)
    if not partner:
        raise VluxApiError("VALIDATION_ERROR", "El cliente %s no existe." % partner_id)
    return partner


def _register_from_body(body):
    try:
        return _register(int(body.get("register_id")))
    except (TypeError, ValueError):
        raise VluxApiError("VALIDATION_ERROR", "register_id debe ser un entero.")


class VluxApiSales(http.Controller):

    # --- the register -------------------------------------------------------

    @api_route("/registers/<int:register_id>/session", scope="system:read",
               summary="Estado de la caja y su sesión actual")
    def register_session(self, token, register_id, **kwargs):
        """The register and its current session (``null`` when closed).

        ``employee_login`` says whether sales, openings and closings must carry
        an ``employee_id``; ``max_difference`` is the closing limit.
        """
        return _register_state(_register(register_id))

    @api_route("/registers/<int:register_id>/employees", scope="orders:write",
               summary="Empleados que pueden usar la caja, con PIN cifrado")
    def register_employees(self, token, register_id, **kwargs):
        """Employees allowed on the register, with their role and SHA-1 PIN/badge.

        The register checks a PIN without the network, as the Odoo POS does.
        Empty when the register does not use employee login.
        """
        return {"items": _register(register_id)._vlux_api_employees()}

    @api_route("/registers/<int:register_id>/session/open", scope="session:manage", methods=("POST",),
               summary="Abrir la caja con el efectivo inicial")
    def open_session(self, token, register_id, **kwargs):
        """Open the register: ``{"opening_cash", "employee_id"?, "notes"?}``.

        Safe to repeat: if the session is already open it is returned with
        ``already_open: true`` and nothing changes. ``CONFLICT`` while the
        register is being closed.
        """
        config = _register(register_id)
        body = json_body()
        session = config.current_session_id
        if session and session.state == "opened":
            return {**_register_state(config), "already_open": True}
        if session and session.state != "opening_control":
            raise VluxApiError("CONFLICT", "La caja está en corte; termina el cierre antes de abrirla.",
                               details={"session_id": session.id, "state": session.state})
        employee = config._vlux_api_employee(body.get("employee_id"))
        opening_cash = _amount(body.get("opening_cash"), "opening_cash", required=config.cash_control) or 0.0
        notes = _text(body.get("notes"), "notes")
        if not session:
            config._check_before_creating_new_session()
            session = request.env["pos.session"].create({"user_id": request.env.uid, "config_id": config.id})
        if employee:
            session.sudo().employee_id = employee
        session.set_opening_control(opening_cash, notes)
        return {**_register_state(config), "already_open": False}

    @api_route("/registers/<int:register_id>/session/closing", scope="session:manage",
               summary="Lo esperado en el corte de la sesión abierta")
    def closing_summary(self, token, register_id, **kwargs):
        """What the closing expects: sales, cash (opening, sales, moves) and other methods."""
        config = _register(register_id)
        session = config.current_session_id
        if not session:
            raise VluxApiError("NO_OPEN_SESSION", "La caja no tiene una sesión abierta.")
        data = session.get_closing_control_data()
        cash = data["default_cash_details"]
        return {
            "session": _session_payload(session),
            "max_difference": data["amount_authorized_diff"],
            "orders": {"count": data["orders_details"]["quantity"], "amount": data["orders_details"]["amount"]},
            "cash": {
                "payment_method_id": cash["id"],
                "name": cash["name"],
                "opening": cash["opening"],
                "sales": cash["payment_amount"],
                "moves": [{"name": move["name"], "amount": move["amount"]} for move in cash["moves"]],
                "expected": cash["amount"],
            } if cash else None,
            "other_methods": [
                {"payment_method_id": method["id"], "name": method["name"], "type": method["type"],
                 "expected": method["amount"], "count": method["number"]}
                for method in data["non_cash_payment_methods"]
            ],
        }

    @api_route("/registers/<int:register_id>/session/close", scope="session:manage", methods=("POST",),
               summary="Cerrar la caja (corte) con lo contado")
    def close_session(self, token, register_id, **kwargs):
        """Close the register: ``{"session_id", "counted_cash", "counted"?, "employee_id"?, "notes"?}``.

        ``counted`` lists what was counted for other methods
        (``[{"payment_method_id", "amount"}]``); a method left out is taken as
        matching. Above the register's difference limit only a manager may
        close: the refusal is ``CLOSING_REFUSED`` and the session stays open.
        ``session_id`` must be the open session, so a stale screen cannot
        close a session it never saw.
        """
        config = _register(register_id)
        body = json_body()
        session = config.current_session_id
        if not session:
            raise VluxApiError("NO_OPEN_SESSION", "La caja no tiene una sesión abierta.")
        if body.get("session_id") != session.id:
            raise VluxApiError("CONFLICT", "La sesión indicada no es la sesión abierta de la caja.",
                               details={"session_id": session.id})
        employee = config._vlux_api_employee(body.get("employee_id"))
        notes = _text(body.get("notes"), "notes")
        if employee:
            # The person who counts is the one the closing rules judge.
            session.sudo().employee_id = employee
        if config.cash_control:
            counted_cash = _amount(body.get("counted_cash"), "counted_cash")
            result = session.post_closing_cash_details(counted_cash)
            if not result.get("successful"):
                raise VluxApiError("CLOSING_REFUSED", result.get("message") or "No se pudo registrar el efectivo.")
        expected = {
            method["id"]: method["amount"]
            for method in session.get_closing_control_data()["non_cash_payment_methods"]
            if method["type"] == "bank"
        }
        counted = body.get("counted") or []
        if not isinstance(counted, list):
            raise VluxApiError("VALIDATION_ERROR", "counted debe ser una lista.")
        differences = []
        for entry in counted:
            if not isinstance(entry, dict):
                raise VluxApiError("VALIDATION_ERROR", "Cada elemento de counted debe ser un objeto.")
            method_id = entry.get("payment_method_id")
            if method_id not in expected:
                raise VluxApiError("VALIDATION_ERROR", "La forma de pago %s no se cuenta en el corte." % method_id)
            amount = _amount(entry.get("amount"), "amount")
            difference = config.currency_id.round(amount - expected[method_id])
            if not config.currency_id.is_zero(difference):
                differences.append((method_id, difference))
        session.update_closing_control_state_session(notes)
        result = session.close_session_from_ui(differences)
        if not result.get("successful"):
            # Raising rolls the whole closing back: the session stays open.
            raise VluxApiError("CLOSING_REFUSED", result.get("message") or "No se pudo cerrar la caja.",
                               details={"redirect": bool(result.get("redirect"))})
        return {**_register_state(config), "closed_session": _session_payload(session)}

    # --- selling ------------------------------------------------------------

    @api_route("/orders/quote", scope="orders:write", methods=("POST",),
               summary="Precios, impuestos y total de un carrito, calculados en el servidor")
    def quote(self, token, **kwargs):
        """Price a cart without recording it: ``{"register_id", "lines", "partner_id"?}``.

        ``lines``: ``[{"product_id", "qty", "price_unit"?}]``. Without
        ``price_unit`` the register's pricelist decides; with it, the line is
        priced as sent and ``price_overridden`` tells whether it differs from
        the catalog.
        """
        body = json_body()
        config = _register_from_body(body)
        quote = config._vlux_api_quote(body.get("lines"), _partner(body.get("partner_id")))
        return _quote_payload(quote)

    @api_route("/orders", scope="orders:write", methods=("POST",),
               summary="Registrar una venta pagada (idempotente por uuid)")
    def create_order(self, token, **kwargs):
        """Record a paid sale. Sending the same ``uuid`` again returns the recorded sale.

        Body: ``{"uuid", "register_id", "lines", "payments", "employee_id"?,
        "partner_id"?, "session_id"?, "expected_total"?, "created_at"?}``.
        Totals are the server's. ``TOTAL_MISMATCH`` when ``expected_total``
        differs, ``PAYMENT_MISMATCH`` when the payments do not cover the total
        or card overpays, ``NO_OPEN_SESSION`` when the register is closed:
        nothing is written and the register keeps the sale queued.
        """
        body = json_body()
        config = _register_from_body(body)
        try:
            order, duplicate = request.env["pos.order"]._vlux_api_register_sale(config, body)
        except VluxSaleRefused as refusal:
            raise VluxApiError(refusal.code, refusal.message, details=refusal.details)
        return {**order._vlux_api_payload(), "duplicate": duplicate}

    @api_route("/orders/<string:uuid>", scope="orders:write", summary="Una venta por su uuid")
    def get_order(self, token, uuid, **kwargs):
        """The sale recorded with ``uuid``: how a register confirms what reached the server."""
        order = request.env["pos.order"]._vlux_api_find(uuid)
        if not order or order.company_id != request.env.company:
            raise VluxApiError("NOT_FOUND", _("No hay una venta con ese uuid."))
        return order._vlux_api_payload()
