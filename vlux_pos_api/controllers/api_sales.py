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
from odoo.addons.vlux_pos_api.models.employee_session import LOCK_MINUTES, PIN_ATTEMPTS
from odoo.addons.vlux_pos_api.models.pos_order import VluxSaleRefused

# The employee session token (POST /registers/<id>/employees/login) travels here.
EMPLOYEE_HEADER = "X-Vlux-Employee"


def _register(register_id, token):
    """The register of the token's company, or NOT_FOUND; FORBIDDEN for another register's token."""
    config = request.env["pos.config"].search(
        [("id", "=", register_id), ("company_id", "=", request.env.company.id)], limit=1,
    )
    if not config:
        raise VluxApiError("NOT_FOUND", "La caja no existe.")
    if not token.allows_register(config):
        raise VluxApiError("FORBIDDEN", "Este token es de otra caja.")
    return config


def _acting_employee(config, token, body, required=False, session_raw=None):
    """Who is at the register: ``(employee, verified)``.

    ``verified`` means the server checked the PIN (a valid employee session
    for this register and device). ``required`` refuses anything else: for
    actions that need to know for real (open, close, credit). Registers
    without employee login act as the token's user.
    """
    Employee = request.env["hr.employee"]
    if not config.module_pos_hr:
        return Employee, True
    raw = session_raw if session_raw is not None else request.httprequest.headers.get(EMPLOYEE_HEADER, "")
    claimed = body.get("employee_id")
    if raw:
        employee = request.env["vlux.pos.employee.session"]._resolve(raw, config, token)
        if employee:
            if claimed not in (None, "", employee.id):
                raise VluxApiError("VALIDATION_ERROR", "El empleado no coincide con su sesión.")
            return employee, True
        if required:
            raise VluxApiError("PIN_REQUIRED", "Tu sesión venció: vuelve a entrar con tu PIN.")
    elif required:
        raise VluxApiError("PIN_REQUIRED", "Entra con tu PIN (con internet) para hacer esto.")
    return config._vlux_api_employee(claimed), False


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


def _register_from_body(body, token):
    try:
        register_id = int(body.get("register_id"))
    except (TypeError, ValueError):
        raise VluxApiError("VALIDATION_ERROR", "register_id debe ser un entero.")
    return _register(register_id, token)


class VluxApiSales(http.Controller):

    # --- the register -------------------------------------------------------

    @api_route("/registers/<int:register_id>/session", scope="system:read",
               summary="Estado de la caja y su sesión actual")
    def register_session(self, token, register_id, **kwargs):
        """The register and its current session (``null`` when closed).

        ``employee_login`` says whether sales, openings and closings must carry
        an ``employee_id``; ``max_difference`` is the closing limit.
        """
        return _register_state(_register(register_id, token))

    @api_route("/registers/<int:register_id>/employees", scope="orders:write",
               summary="Empleados que pueden usar la caja, con PIN cifrado")
    def register_employees(self, token, register_id, **kwargs):
        """Employees allowed on the register, with their role and SHA-1 PIN/badge.

        The register checks a PIN without the network, as the Odoo POS does.
        Empty when the register does not use employee login.
        """
        return {"items": _register(register_id, token)._vlux_api_employees()}

    @api_route("/registers/<int:register_id>/employees/login", scope="orders:write", methods=("POST",),
               summary="Entrar a la caja con PIN, verificado por el servidor")
    def employee_login(self, token, register_id, **kwargs):
        """Body ``{"employee_id", "pin"}``: answers a session token for header ``X-Vlux-Employee``.

        The PIN is compared on the server. After 5 wrong PINs the employee is
        locked on this register for 15 minutes (``PIN_LOCKED``). A manager
        must have a PIN. The session lasts a shift (12 h) and only works on
        this register and with this device's token.
        """
        body = json_body()
        config = _register(register_id, token)
        if not config.module_pos_hr:
            raise VluxApiError("VALIDATION_ERROR", "Esta caja no usa inicio de sesión por empleado.")
        employee = config._vlux_api_employee(body.get("employee_id"))
        limiter = request.env["vlux.rate.limit"].sudo()
        identity = "%s:%s" % (config.id, employee.id)
        if limiter.exhausted("pin", identity, PIN_ATTEMPTS, LOCK_MINUTES * 60):
            raise VluxApiError("PIN_LOCKED", "Demasiados PIN incorrectos: espera %d minutos." % LOCK_MINUTES)
        manager = config._vlux_api_is_manager(employee)
        if manager and not employee.sudo().pin:
            raise VluxApiError("VALIDATION_ERROR",
                               "%s es encargado y no tiene PIN: ponle uno en Odoo." % employee.name)
        Session = request.env["vlux.pos.employee.session"]
        if not Session._pin_matches(employee, body.get("pin")):
            limiter.consume("pin", identity, PIN_ATTEMPTS, LOCK_MINUTES * 60)
            raise VluxApiError("INVALID_PIN", "PIN incorrecto.")
        limiter.reset("pin", identity)
        session, raw = Session._issue(config, employee, token)
        return {
            "session": raw,
            "expires_at": _stamp(session.expires_at),
            "employee": {"id": employee.id, "name": employee.name, "role": "manager" if manager else "cashier"},
        }

    @api_route("/registers/<int:register_id>/employees/logout", scope="orders:write", methods=("POST",),
               summary="Salir: la sesión del empleado deja de servir")
    def employee_logout(self, token, register_id, **kwargs):
        """Ends the employee session sent in ``X-Vlux-Employee`` (idempotent)."""
        _register(register_id, token)
        raw = request.httprequest.headers.get(EMPLOYEE_HEADER, "")
        Session = request.env["vlux.pos.employee.session"].sudo()
        Session.search([("token_hash", "=", Session._hash(raw))]).write({"active": False}) if raw else None
        return {"ended": True}

    @api_route("/registers/<int:register_id>/session/open", scope="session:manage", methods=("POST",),
               summary="Abrir la caja con el efectivo inicial")
    def open_session(self, token, register_id, **kwargs):
        """Open the register: ``{"opening_cash", "employee_id"?, "notes"?}``.

        Safe to repeat: if the session is already open it is returned with
        ``already_open: true`` and nothing changes. ``CONFLICT`` while the
        register is being closed.
        """
        config = _register(register_id, token)
        body = json_body()
        session = config.current_session_id
        if session and session.state == "opened":
            return {**_register_state(config), "already_open": True}
        if session and session.state != "opening_control":
            raise VluxApiError("CONFLICT", "La caja está en corte; termina el cierre antes de abrirla.",
                               details={"session_id": session.id, "state": session.state})
        employee, _verified = _acting_employee(config, token, body, required=True)
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
        config = _register(register_id, token)
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
            # Extra sections from other modules (credit: who was given credit, abonos).
            **session._vlux_api_closing_extra(),
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
        config = _register(register_id, token)
        body = json_body()
        session = config.current_session_id
        if not session:
            raise VluxApiError("NO_OPEN_SESSION", "La caja no tiene una sesión abierta.")
        if body.get("session_id") != session.id:
            raise VluxApiError("CONFLICT", "La sesión indicada no es la sesión abierta de la caja.",
                               details={"session_id": session.id})
        employee, _verified = _acting_employee(config, token, body, required=True)
        config._vlux_api_check_closer(employee)
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
        config = _register_from_body(body, token)
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
        config = _register_from_body(body, token)
        # A queued sale carries the session it was made under; a sale whose
        # PIN was only checked offline is kept and marked unverified.
        employee, verified = _acting_employee(
            config, token, body, session_raw=body.get("employee_session") or None,
        )
        try:
            order, duplicate = request.env["pos.order"]._vlux_api_register_sale(
                config, body, employee=employee, verified=verified,
            )
        except VluxSaleRefused as refusal:
            raise VluxApiError(refusal.code, refusal.message, details=refusal.details)
        return {**order._vlux_api_payload(), "duplicate": duplicate}

    # --- returns ----------------------------------------------------------------------

    @api_route("/orders/recent", scope="orders:write", summary="Ventas recientes de la caja (devolver, reimprimir)",
               params=[{"name": "register_id", "in": "query", "schema": {"type": "integer"}},
                       {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 30, "maximum": 100}}])
    def recent_orders(self, token, register_id=None, limit=None, **kwargs):
        """The register's latest sales and returns, newest first (default 30)."""
        try:
            config = _register(int(register_id), token)
        except (TypeError, ValueError):
            raise VluxApiError("VALIDATION_ERROR", "register_id debe ser un entero.")
        try:
            limit = max(1, min(100, int(limit or 30)))
        except (TypeError, ValueError):
            limit = 30
        orders = request.env["pos.order"].search(
            [("config_id", "=", config.id), ("state", "in", ("paid", "done", "invoiced"))],
            order="date_order desc, id desc", limit=limit,
        )
        return {"items": [order._vlux_api_payload() for order in orders]}

    @api_route("/orders/lookup", scope="orders:write", summary="Buscar una venta por su folio",
               params=[{"name": "reference", "in": "query", "schema": {"type": "string"}}])
    def lookup_order(self, token, reference=None, **kwargs):
        """Sales of the token's company whose folio (or name) matches ``reference``."""
        reference = (reference or "").strip()
        if len(reference) < 3 or len(reference) > 64:
            raise VluxApiError("VALIDATION_ERROR", "Escribe el folio del ticket.")
        orders = request.env["pos.order"].search(
            [("company_id", "=", request.env.company.id), ("is_refund", "=", False),
             ("state", "in", ("paid", "done", "invoiced")),
             "|", ("pos_reference", "ilike", reference), ("name", "ilike", reference)],
            order="date_order desc", limit=10,
        )
        if token.pos_config_id:
            orders = orders.filtered(lambda order: token.allows_register(order.config_id))
        return {"items": [order._vlux_api_payload() for order in orders]}

    def _refund_original(self, body):
        try:
            order_id = int(body.get("order_id"))
        except (TypeError, ValueError):
            raise VluxApiError("VALIDATION_ERROR", "order_id debe ser un entero.")
        original = request.env["pos.order"].search(
            [("id", "=", order_id), ("company_id", "=", request.env.company.id), ("is_refund", "=", False)], limit=1,
        )
        if not original:
            raise VluxApiError("NOT_FOUND", "La venta no existe.")
        return original

    @api_route("/orders/refund/quote", scope="orders:write", methods=("POST",),
               summary="Cuánto se devuelve de una venta (precios e impuestos de la venta)")
    def refund_quote(self, token, **kwargs):
        """Body ``{"register_id", "order_id", "lines": [{"line_id", "qty"}]}``; nothing is recorded."""
        body = json_body()
        _register_from_body(body, token)
        original = self._refund_original(body)
        Order = request.env["pos.order"]
        quote = Order._vlux_api_refund_quote(original, Order._vlux_api_refund_lines(original, body.get("lines")))
        return {
            "order_id": original.id,
            "amount_refund": -quote["amount_total"],
            "amount_tax": -quote["amount_tax"],
            "lines": [{"line_id": row["line"].id, "qty": -row["qty"], "amount": -row["price_subtotal_incl"]}
                      for row in quote["lines"]],
            "paid_with": [{"payment_method_id": payment.payment_method_id.id, "name": payment.payment_method_id.name,
                           "type": payment.payment_method_id.type, "amount": payment.amount}
                          for payment in original.payment_ids if not payment.is_change],
        }

    @api_route("/orders/refund", scope="orders:write", methods=("POST",),
               summary="Registrar una devolución (idempotente por uuid; requiere PIN)")
    def create_refund(self, token, **kwargs):
        """Body ``{"uuid", "register_id", "order_id", "lines", "payments"}``.

        Money goes back with ``payments`` (positive amounts, summing exactly
        the return). Needs an employee session: cash leaves the drawer.
        """
        body = json_body()
        config = _register_from_body(body, token)
        employee, verified = _acting_employee(config, token, body, required=True)
        original = self._refund_original(body)
        try:
            order, duplicate = request.env["pos.order"]._vlux_api_register_refund(
                config, original, body, employee, verified,
            )
        except VluxSaleRefused as refusal:
            raise VluxApiError(refusal.code, refusal.message, details=refusal.details)
        return {**order._vlux_api_payload(), "duplicate": duplicate}

    @api_route("/orders/<string:uuid>", scope="orders:write", summary="Una venta por su uuid")
    def get_order(self, token, uuid, **kwargs):
        """The sale recorded with ``uuid``: how a register confirms what reached the server."""
        order = request.env["pos.order"]._vlux_api_find(uuid)
        if not order or order.company_id != request.env.company or not token.allows_register(order.config_id):
            raise VluxApiError("NOT_FOUND", _("No hay una venta con ese uuid."))
        return order._vlux_api_payload()
