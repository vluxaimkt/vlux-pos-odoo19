"""Credit through the VLUX API (the register PWA), on the same rules as the Odoo POS.

The API sale path (``vlux_pos_api``) refuses the customer-account method by
default; with this module it accepts the store's "Crédito" method. As on the
Odoo POS, the server never refuses a credit sale for breaking a rule (the
goods left the store, maybe offline): ``_vlux_check_credit`` flags it for the
owner. The only hard requirement is a customer, without whom there is no one
to owe the money.
"""
from collections import defaultdict

from odoo import _, api, models
from odoo.exceptions import ValidationError

from .pos_payment_method import CREDIT_ORDER_STATES


class PosOrder(models.Model):
    _inherit = "pos.order"

    @api.model
    def _vlux_api_method_refusal(self, method):
        if method.type == "pay_later":
            if not method.split_transactions:
                return _("La forma de pago Crédito debe pedir el cliente (transacciones divididas).")
            return None
        return super()._vlux_api_method_refusal(method)

    @api.model
    def _vlux_api_before_sale(self, config, partner, payments):
        super()._vlux_api_before_sale(config, partner, payments)
        if not partner and any(payment["method"].type == "pay_later" for payment in payments):
            raise ValidationError(_("Elige al cliente al que se le fía."))

    def _vlux_sold_by_manager(self):
        # A VLUX register that could not prove who sold (PIN checked offline)
        # counts as a cashier: its sales on credit are flagged for the owner.
        self.ensure_one()
        if (self.source == "vlux_api" and not self.vlux_api_employee_verified
                and self.session_id.config_id.vlux_credit_sellers != "all"):
            return False
        return super()._vlux_sold_by_manager()

    @api.model
    def _vlux_api_register_sale(self, config, data, employee=None, verified=True):
        # The balance before this sale is what the ticket prints as "saldo
        # anterior"; the real balance is always recomputed from payments.
        previous = None
        if data.get("partner_id"):
            partner = self.env["res.partner"].search([("id", "=", data["partner_id"])], limit=1)
            if partner:
                previous = partner.sudo().vlux_credit_balance
        order, duplicate = super()._vlux_api_register_sale(config, data, employee=employee, verified=verified)
        if not duplicate and previous is not None and order.vlux_credit_amount:
            order.sudo().vlux_credit_prev_balance = previous
        return order, duplicate

    def _vlux_api_payload(self):
        payload = super()._vlux_api_payload()
        payload["credit"] = self._vlux_api_credit_payload()
        return payload

    def _vlux_api_credit_payload(self):
        """Credit figures of a sale on credit or an abono (None otherwise)."""
        self.ensure_one()
        amount = self.vlux_credit_amount
        if not amount and not self.vlux_credit_abono:
            return None
        previous = self.vlux_credit_prev_balance
        return {
            "amount": amount,
            "abono": self.vlux_credit_abono,
            "previous_balance": previous,
            "new_balance": self.currency_id.round(previous + amount),
            "flagged": self.vlux_credit_flagged,
            "issues": (self.vlux_credit_issues or "").splitlines(),
        }


class PosSession(models.Model):
    _inherit = "pos.session"

    def _vlux_api_closing_extra(self):
        """Who was given credit and who paid back in this session, for the closing slip.

        The net credit of the day hides both: a sale on credit and an abono of
        the same amount cancel out. Here each one is listed with the customer.
        """
        extra = super()._vlux_api_closing_extra()
        orders = self.env["pos.order"].search(
            [("session_id", "=", self.id), ("state", "in", CREDIT_ORDER_STATES)], order="date_order, id",
        )
        currency = self.currency_id
        sales, abonos = [], []
        for order in orders:
            reference = order.pos_reference or order.name
            customer = order.partner_id.name or ""
            if order.vlux_credit_abono:
                paid = order.payment_ids.filtered(lambda payment: payment.payment_method_id.type != "pay_later")[:1]
                abonos.append({"customer": customer, "reference": reference, "method": paid.payment_method_id.name or "",
                               "amount": currency.round(paid.amount)})
            elif order.vlux_credit_amount:
                sales.append({"customer": customer, "reference": reference, "amount": currency.round(order.vlux_credit_amount),
                              "flagged": order.vlux_credit_flagged})
        extra["credit"] = {
            "sales": sales,
            "abonos": abonos,
            "total_sales": currency.round(sum(row["amount"] for row in sales)),
            "total_abonos": currency.round(sum(row["amount"] for row in abonos)),
        }
        return extra


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _vlux_api_credit_row(self, balance=None):
        """One customer's credit, as the register shows it."""
        self.ensure_one()
        partner = self.sudo()
        if balance is None:
            # Computed, not stored: an abono or sale in this same request
            # must show in the balance.
            partner.invalidate_recordset(["vlux_credit_balance"])
        currency = partner.vlux_credit_currency_id
        balance = currency.round(partner.vlux_credit_balance if balance is None else balance)
        limit = partner.vlux_credit_limit
        return {
            "partner_id": self.id,
            "name": self.name,
            "phone": self.phone or None,
            "allowed": partner.vlux_credit_allowed,
            "limit": limit,
            "balance": balance,
            # None when there is no limit.
            "available": currency.round(limit - balance) if limit else None,
            "over_limit": bool(limit) and currency.compare_amounts(balance, limit) > 0,
        }

    @api.model
    def _vlux_api_credit_rows(self):
        """Customers authorised for credit or owing money, in the caller's company.

        Balances come from the POS payments on customer account, the same
        source as the register and the owner's report. The customers are read
        as the caller, so record rules decide who is listed.
        """
        company = self.env.company
        groups = self.env["pos.payment"].sudo()._read_group(
            [
                ("payment_method_id.journal_id", "=", False),
                ("pos_order_id.state", "in", CREDIT_ORDER_STATES),
                ("pos_order_id.company_id", "=", company.id),
                ("pos_order_id.partner_id", "!=", False),
            ],
            groupby=["pos_order_id"],
            aggregates=["amount:sum"],
        )
        balances = defaultdict(float)
        for order, amount in groups:
            balances[order.partner_id.id] += amount
        owing = [pid for pid, balance in balances.items() if not company.currency_id.is_zero(balance)]
        partners = self.search(["|", ("vlux_credit_allowed", "=", True), ("id", "in", owing)], order="name, id")
        return [partner._vlux_api_credit_row(balances.get(partner.id, 0.0)) for partner in partners]
