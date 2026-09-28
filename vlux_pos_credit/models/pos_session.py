"""Abonos: a customer pays back (part of) what they owe, at the register.

Odoo Community sells on customer account but settling it from the register
(pos_settle_due) is Enterprise. An abono here is an order of the open session
without products and two payments: +amount in the method the customer paid
with (cash enters the closing count) and -amount on the customer account (the
debt goes down). The closing books it like any order: cash in, customer
receivable down.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import float_compare, float_round

ABONO_MAX = 10_000_000.0


class PosSession(models.Model):
    _inherit = "pos.session"

    @api.model
    def vlux_pos_register_abono(self, session_id, partner_id, amount, payment_method_id, uuid, employee_id=False):
        """Register an abono and return what its ticket prints.

        Idempotent on ``uuid``: a register that retries after a dropped
        connection gets the same abono back, never a second one.
        """
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(_("Se requiere un usuario de punto de venta."))
        session = self.browse(int(session_id)).exists()
        if not session:
            raise ValidationError(_("La caja no existe."))
        session.check_access("read")
        if session.state != "opened":
            raise ValidationError(_("La caja no está abierta: abre la caja para recibir abonos."))
        config = session.config_id
        uuid = str(uuid or "").strip()
        if not uuid or len(uuid) > 64:
            raise ValidationError(_("Solicitud de abono inválida."))

        Order = self.env["pos.order"]
        existing = Order.search([("uuid", "=", uuid)], limit=1)
        if existing:
            if not existing.vlux_credit_abono or existing.session_id != session:
                raise ValidationError(_("Solicitud de abono inválida."))
            return existing._vlux_abono_payload()

        partner = self.env["res.partner"].browse(int(partner_id)).exists()
        if not partner:
            raise ValidationError(_("El cliente no existe."))
        partner.check_access("read")
        method = config.payment_method_ids.filtered(lambda m: m.id == int(payment_method_id))
        if not method or method.type == "pay_later":
            raise ValidationError(_("Elige cómo paga el cliente: efectivo o tarjeta."))
        credit = config.payment_method_ids.filtered(lambda m: m.type == "pay_later")[:1]
        if not credit:
            raise ValidationError(_("Esta caja no tiene la forma de pago Crédito."))
        employee = self.env["hr.employee"].sudo().browse(int(employee_id)).exists() if employee_id else None
        if config.module_pos_hr and not employee:
            raise ValidationError(_("Entra a la caja con tu NIP para recibir abonos."))
        if employee and employee.company_id and employee.company_id != config.company_id:
            raise AccessError(_("El empleado no pertenece a esta tienda."))

        currency = config.currency_id
        try:
            amount = float_round(float(amount), precision_rounding=currency.rounding)
        except (TypeError, ValueError):
            raise ValidationError(_("El abono debe ser una cantidad en pesos."))
        if float_compare(amount, 0.0, precision_rounding=currency.rounding) <= 0 or amount > ABONO_MAX:
            raise ValidationError(_("El abono debe ser mayor a cero."))
        balance = partner.sudo().vlux_credit_balance
        if float_compare(amount, balance, precision_rounding=currency.rounding) > 0:
            raise ValidationError(_(
                "%(name)s debe %(balance)s: el abono no puede ser mayor.",
                name=partner.name, balance=currency.format(balance),
            ))

        now = fields.Datetime.now()
        values = {
            "uuid": uuid,
            "session_id": session.id,
            "partner_id": partner.id,
            "user_id": self.env.uid,
            "pricelist_id": config.pricelist_id.id,
            "fiscal_position_id": False,
            "date_order": fields.Datetime.to_string(now),
            "lines": [],
            "payment_ids": [
                (0, 0, {"amount": amount, "payment_method_id": method.id, "payment_date": now}),
                (0, 0, {"amount": -amount, "payment_method_id": credit.id, "payment_date": now}),
            ],
            "amount_total": 0.0,
            "amount_tax": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
            "state": "paid",
            "to_invoice": False,
            "last_order_preparation_change": "{}",
            "vlux_credit_abono": True,
            "vlux_credit_prev_balance": balance,
        }
        if employee:
            values["employee_id"] = employee.id
        Order.sync_from_ui([values])
        order = Order.search([("uuid", "=", uuid)], limit=1)
        if not order or order.state not in ("paid", "done", "invoiced"):
            raise ValidationError(_("No se pudo registrar el abono."))
        return order._vlux_abono_payload()
