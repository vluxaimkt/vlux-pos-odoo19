"""Sales registered through the VLUX API.

Every sale goes through Odoo's own ``pos.order.sync_from_ui``: the same code
path, hooks and accounting as a sale made on the Odoo POS. What this layer
adds is what an untrusted, sometimes offline client needs:

* **Idempotency by ``uuid``.** The register generates the uuid; sending the
  same sale again returns the order already recorded instead of a second one.
* **Totals from the server.** Prices, taxes and totals are computed here
  (``pos.config._vlux_api_quote``); the client's own total is only checked.
  A sale whose payments do not cover the total is refused with nothing
  written, and the register keeps it in its queue.
* **Honest prices.** A unit price that differs from the catalog is accepted
  (an offline register charges what its local copy says) and flagged on the
  order for the owner.
"""
import uuid as uuid_lib
from datetime import timedelta

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# How far in the future a client clock may be before its date is ignored.
CLOCK_SKEW = timedelta(minutes=5)


class VluxSaleRefused(Exception):
    """A sale the API answers with a specific error code; nothing was written."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _valid_uuid(value, name="uuid"):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 64:
        raise ValidationError(_("%s debe ser un texto de 1 a 64 caracteres.", name))
    return value.strip()


class PosOrder(models.Model):
    _inherit = "pos.order"

    source = fields.Selection(selection_add=[("vlux_api", "VLUX")], ondelete={"vlux_api": "set default"})
    vlux_api_price_overridden = fields.Boolean(
        string="Precio distinto al catálogo",
        readonly=True,
        copy=False,
        help="La caja VLUX cobró al menos un producto a un precio distinto al del catálogo.",
    )

    @api.model
    def _vlux_api_find(self, uuid):
        return self.search([("uuid", "=", uuid)], limit=1)

    @api.model
    def _vlux_api_register_sale(self, config, data):
        """Record ``data`` as a paid sale of ``config``; return ``(order, duplicate)``.

        ``data`` is the request body of ``POST /orders`` (see ``docs/API_V1.md``).
        """
        uuid = _valid_uuid(data.get("uuid"))
        existing = self._vlux_api_find(uuid)
        if existing:
            if existing.config_id != config:
                raise VluxSaleRefused("CONFLICT", _("El uuid %s ya pertenece a una venta de otra caja.", uuid),
                                      {"order_id": existing.id, "register_id": existing.config_id.id})
            return existing, True

        session = config.current_session_id
        if not session or session.state != "opened":
            raise VluxSaleRefused("NO_OPEN_SESSION", _("La caja %s no tiene una sesión abierta.", config.name))
        claimed_session = data.get("session_id")
        if claimed_session not in (None, session.id):
            other = self.env["pos.session"].search([("id", "=", claimed_session)], limit=1)
            if other.config_id != config:
                raise ValidationError(_("La sesión %s no es de esta caja.", claimed_session))
            # A sale made before a closing arrives after it: like Odoo's
            # _get_valid_session, it lands in the session open now.

        employee = config._vlux_api_employee(data.get("employee_id"))
        partner = self.env["res.partner"]
        if data.get("partner_id"):
            partner = partner.search([("id", "=", data["partner_id"])], limit=1)
            if not partner:
                raise ValidationError(_("El cliente %s no existe.", data["partner_id"]))

        quote = config._vlux_api_quote(data.get("lines"), partner)
        currency = config.currency_id
        total = quote["amount_total"]
        expected = data.get("expected_total")
        if expected is not None:
            try:
                expected = float(expected)
            except (TypeError, ValueError):
                raise ValidationError(_("expected_total debe ser un número."))
            if currency.compare_amounts(expected, total) != 0:
                raise VluxSaleRefused(
                    "TOTAL_MISMATCH",
                    _("El total de la caja (%(expected)s) no coincide con el del servidor (%(total)s).",
                      expected=currency.format(expected), total=currency.format(total)),
                    {"expected_total": expected, **_quote_details(quote)},
                )

        payments, change = self._vlux_api_payments(config, session, data.get("payments"), total)
        self._vlux_api_before_sale(config, partner, payments)
        now = fields.Datetime.now()
        vals = {
            "uuid": uuid,
            "session_id": session.id,
            "user_id": self.env.uid,
            "partner_id": partner.id or False,
            "pricelist_id": quote["pricelist"].id or False,
            "fiscal_position_id": quote["fiscal_position"].id or False,
            "date_order": fields.Datetime.to_string(_client_date(data.get("created_at"), now)),
            "source": "vlux_api",
            "state": "paid",
            "to_invoice": False,
            "amount_total": total,
            "amount_tax": quote["amount_tax"],
            "amount_paid": sum(payment["amount"] for payment in payments),
            # Odoo books the change as a negative cash payment.
            "amount_return": -change,
            "lines": [
                [0, 0, {
                    "uuid": line["uuid"] or str(uuid_lib.uuid4()),
                    "product_id": line["product"].id,
                    "full_product_name": line["product"].display_name,
                    "qty": line["qty"],
                    "price_unit": line["price_unit"],
                    "price_type": "manual" if line["price_overridden"] else "original",
                    "discount": 0.0,
                    "tax_ids": [[6, 0, line["taxes"].ids]],
                    "price_subtotal": line["price_subtotal"],
                    "price_subtotal_incl": line["price_subtotal_incl"],
                }]
                for line in quote["lines"]
            ],
            "payment_ids": [
                [0, 0, {
                    "uuid": str(uuid_lib.uuid4()),
                    "payment_method_id": payment["method"].id,
                    "amount": payment["amount"],
                    "payment_date": fields.Datetime.to_string(now),
                }]
                for payment in payments
            ],
        }
        if employee:
            vals["employee_id"] = employee.id
        try:
            with self.env.cr.savepoint():
                self.sync_from_ui([vals])
        except psycopg2.errors.UniqueViolation:
            # The same sale is being recorded by a parallel request that has
            # not committed yet: the retry will find it.
            raise VluxSaleRefused("CONFLICT", _("La venta %s se está registrando en otra solicitud; reintenta.", uuid),
                                  {"retry": True})
        order = self._vlux_api_find(uuid)
        if any(line["price_overridden"] for line in quote["lines"]):
            order.sudo().vlux_api_price_overridden = True
        return order, False

    @api.model
    def _vlux_api_method_refusal(self, method):
        """Why ``method`` cannot pay an API sale, or None. vlux_pos_credit lifts it for credit."""
        if method.type == "pay_later":
            return _("Esta caja no vende a crédito por la API.")
        return None

    @api.model
    def _vlux_api_before_sale(self, config, partner, payments):
        """Last check before the sale is recorded (hook; nothing by default)."""

    @api.model
    def _vlux_api_payments(self, config, session, payments, amount_total):
        """Validate the payments against the server total; return them and the change."""
        currency = config.currency_id
        if not isinstance(payments, list) or not payments:
            raise ValidationError(_("La venta no tiene pagos."))
        allowed = session.payment_method_ids
        parsed = []
        for index, payment in enumerate(payments, start=1):
            if not isinstance(payment, dict):
                raise ValidationError(_("El pago %s no es un objeto.", index))
            try:
                method_id = int(payment.get("payment_method_id"))
                amount = currency.round(float(payment.get("amount")))
            except (TypeError, ValueError):
                raise ValidationError(_("El pago %s tiene payment_method_id o amount inválidos.", index))
            method = allowed.filtered(lambda m: m.id == method_id)
            if not method:
                raise ValidationError(_("La forma de pago %s no está en esta caja.", method_id))
            refusal = self._vlux_api_method_refusal(method)
            if refusal:
                raise ValidationError(refusal)
            if currency.compare_amounts(amount, 0.0) <= 0:
                raise ValidationError(_("El pago %s debe ser mayor que cero.", index))
            parsed.append({"method": method, "amount": amount})

        cash = currency.round(sum(p["amount"] for p in parsed if p["method"].is_cash_count))
        paid = currency.round(sum(p["amount"] for p in parsed))
        due = config._vlux_api_amount_due(amount_total, has_cash=bool(cash))
        change = currency.round(paid - due)
        details = {"amount_total": amount_total, "amount_due": due, "amount_paid": paid}
        if currency.compare_amounts(change, 0.0) < 0:
            raise VluxSaleRefused(
                "PAYMENT_MISMATCH",
                _("Los pagos (%(paid)s) no cubren el total (%(due)s).",
                  paid=currency.format(paid), due=currency.format(due)),
                details,
            )
        if currency.compare_amounts(change, cash) > 0:
            raise VluxSaleRefused(
                "PAYMENT_MISMATCH",
                _("Sólo se puede dar cambio de un pago en efectivo; sobran %s.", currency.format(change)),
                details,
            )
        return parsed, change

    def _vlux_api_payload(self):
        """The recorded sale, as the API returns it."""
        self.ensure_one()
        change = -sum(self.payment_ids.filtered("is_change").mapped("amount"))
        return {
            "id": self.id,
            "uuid": self.uuid,
            "name": self.name,
            "pos_reference": self.pos_reference or None,
            "tracking_number": self.tracking_number or None,
            "state": self.state,
            "register_id": self.config_id.id,
            "session_id": self.session_id.id,
            "employee_id": self.employee_id.id or None,
            "partner_id": self.partner_id.id or None,
            "date_order": fields.Datetime.to_string(self.date_order) + "Z",
            "amount_total": self.amount_total,
            "amount_tax": self.amount_tax,
            "amount_paid": self.amount_paid,
            "change": self.currency_id.round(change),
            "price_overridden": self.vlux_api_price_overridden,
            "lines": [
                {
                    "uuid": line.uuid,
                    "product_id": line.product_id.id,
                    "name": line.full_product_name or line.product_id.display_name,
                    "qty": line.qty,
                    "price_unit": line.price_unit,
                    "price_subtotal": line.price_subtotal,
                    "price_subtotal_incl": line.price_subtotal_incl,
                }
                for line in self.lines
            ],
            "payments": [
                {
                    "payment_method_id": payment.payment_method_id.id,
                    "name": payment.payment_method_id.name,
                    "amount": payment.amount,
                    "is_change": payment.is_change,
                }
                for payment in self.payment_ids
            ],
        }


def _client_date(value, now):
    """The sale time the register reports, unless it is unreadable or in the future."""
    if not value:
        return now
    try:
        stamp = fields.Datetime.to_datetime(str(value).replace("T", " ").rstrip("Z")[:19])
    except ValueError:
        return now
    if not stamp or stamp > now + CLOCK_SKEW:
        return now
    return stamp


def _quote_details(quote):
    return {
        "amount_untaxed": quote["amount_untaxed"],
        "amount_tax": quote["amount_tax"],
        "amount_total": quote["amount_total"],
        "lines": [
            {
                "product_id": line["product"].id,
                "qty": line["qty"],
                "price_unit": line["price_unit"],
                "catalog_price": line["catalog_price"],
                "price_subtotal_incl": line["price_subtotal_incl"],
            }
            for line in quote["lines"]
        ],
    }
