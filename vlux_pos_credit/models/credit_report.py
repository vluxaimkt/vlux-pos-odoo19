"""What the owner sees about credit: who owes what, and each customer's statement.

Everything comes from the POS payments on customer account (sales on credit
add, refunds and abonos subtract), the same source as the balances shown at
the register, so the owner panel and the register never disagree.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .pos_payment_method import CREDIT_ORDER_STATES

OWNER_GROUPS = ("vlux_core.group_vlux_owner", "vlux_owner.group_vlux_owner")
FLAGGED_LIMIT = 50
STATEMENT_LIMIT = 500


class VluxCreditReport(models.AbstractModel):
    _name = "vlux.credit.report"
    _description = "VLUX credit report for the owner"

    def _check_owner(self):
        user = self.env.user
        for xmlid in OWNER_GROUPS:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group and group in user.all_group_ids:
                return
        raise AccessError(_("Sólo el dueño puede ver los créditos."))

    def _credit_payments(self, domain=()):
        return self.env["pos.payment"].sudo().search([
            ("payment_method_id.journal_id", "=", False),
            ("pos_order_id.state", "in", CREDIT_ORDER_STATES),
            ("pos_order_id.company_id", "in", self.env.companies.ids),
            *domain,
        ], order="payment_date, id")

    @api.model
    def get_balances(self):
        """Customers who owe, the total to collect, and sales flagged for review."""
        self._check_owner()
        per_partner = {}
        for payment in self._credit_payments():
            partner = payment.pos_order_id.partner_id
            if not partner:
                continue
            row = per_partner.setdefault(partner.id, {
                "partner": partner, "balance": 0.0, "last_purchase": None, "last_payment": None,
            })
            row["balance"] += payment.amount
            key = "last_purchase" if payment.amount > 0 else "last_payment"
            row[key] = payment.payment_date
        currency = self.env.company.currency_id
        customers = []
        for row in per_partner.values():
            if currency.is_zero(row["balance"]):
                continue
            partner = row["partner"]
            customers.append({
                "id": partner.id,
                "name": partner.name,
                "phone": partner.phone or "",
                "balance": currency.round(row["balance"]),
                "limit": partner.vlux_credit_limit,
                "allowed": partner.vlux_credit_allowed,
                "over_limit": bool(partner.vlux_credit_limit) and row["balance"] > partner.vlux_credit_limit,
                "last_purchase": fields.Datetime.to_string(row["last_purchase"]) if row["last_purchase"] else None,
                "last_payment": fields.Datetime.to_string(row["last_payment"]) if row["last_payment"] else None,
            })
        customers.sort(key=lambda customer: customer["balance"], reverse=True)
        flagged = self.env["pos.order"].sudo().search([
            ("vlux_credit_flagged", "=", True), ("company_id", "in", self.env.companies.ids),
        ], order="date_order desc", limit=FLAGGED_LIMIT)
        return {
            "total_owed": currency.round(sum(customer["balance"] for customer in customers if customer["balance"] > 0)),
            "customers": customers,
            "flagged": [{
                "reference": order.pos_reference or order.name,
                "date": fields.Datetime.to_string(order.date_order),
                "customer": order.partner_id.name or "",
                "amount": order.vlux_credit_amount,
                "cashier": order.employee_id.name or order.user_id.name,
                "issues": (order.vlux_credit_issues or "").splitlines(),
            } for order in flagged],
        }

    @api.model
    def get_statement(self, partner_id):
        """A customer's account: every purchase on credit, abono and refund, with the running balance."""
        self._check_owner()
        partner = self.env["res.partner"].browse(int(partner_id)).exists()
        if not partner:
            raise AccessError(_("El cliente no existe."))
        payments = self._credit_payments([("pos_order_id.partner_id", "=", partner.id)])
        currency = self.env.company.currency_id
        balance = 0.0
        moves = []
        for payment in payments:
            order = payment.pos_order_id
            balance += payment.amount
            if order.vlux_credit_abono:
                paid_with = order.payment_ids.filtered(lambda p: p.payment_method_id.journal_id)[:1]
                kind = _("Abono (%s)", paid_with.payment_method_id.name) if paid_with else _("Abono")
            elif payment.amount < 0:
                kind = _("Devolución")
            else:
                kind = _("Compra a crédito")
            moves.append({
                "date": fields.Datetime.to_string(payment.payment_date),
                "reference": order.pos_reference or order.name,
                "kind": kind,
                "charge": currency.round(payment.amount) if payment.amount > 0 else 0.0,
                "payment": currency.round(-payment.amount) if payment.amount < 0 else 0.0,
                "balance": currency.round(balance),
                "cashier": order.employee_id.name or order.user_id.name,
            })
        # The running balance needs every move; the screen shows the latest.
        moves = moves[-STATEMENT_LIMIT:]
        return {
            "customer": {
                "id": partner.id, "name": partner.name, "phone": partner.phone or "",
                "limit": partner.vlux_credit_limit, "allowed": partner.vlux_credit_allowed,
            },
            "company": self.env.company.name,
            "balance": currency.round(balance),
            "moves": moves,
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
        }
