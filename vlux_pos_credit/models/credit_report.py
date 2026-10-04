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
        for opening in self.env["vlux.credit.opening"].sudo().search(
                [("state", "=", "posted"), ("company_id", "in", self.env.companies.ids)]):
            row = per_partner.setdefault(opening.partner_id.id, {
                "partner": opening.partner_id, "balance": 0.0, "last_purchase": None, "last_payment": None,
            })
            row["balance"] += opening.amount
            opened = fields.Datetime.to_datetime(opening.date)
            if not row["last_purchase"] or opened > row["last_purchase"]:
                row["last_purchase"] = opened
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
    def _statement_items(self, order):
        currency = order.currency_id
        return [{
            "name": line.full_product_name or line.product_id.display_name,
            "qty": line.qty,
            "price_unit": currency.round(line.price_subtotal_incl / line.qty) if line.qty else 0.0,
            "total": currency.round(line.price_subtotal_incl),
        } for line in order.lines]

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
        # The notebook's balance goes first, on its date, among the register's moves.
        openings = self.env["vlux.credit.opening"]._vlux_posted([partner.id])
        events = sorted(
            [(fields.Datetime.to_datetime(opening.date), 0, opening) for opening in openings]
            + [(payment.payment_date, 1, payment) for payment in payments],
            key=lambda event: (event[0], event[1], event[2].id),
        )
        for when, kind_order, record in events:
            if kind_order == 0:
                balance += record.amount
                moves.append({
                    "date": fields.Datetime.to_string(when),
                    "reference": record.note or "",
                    "kind": _("Saldo inicial (libreta)"),
                    "items": [],
                    "ticket_total": currency.round(record.amount),
                    "charge": currency.round(record.amount),
                    "payment": 0.0,
                    "balance": currency.round(balance),
                    "cashier": "",
                })
                continue
            payment = record
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
                # What was bought (or returned): the customer recognises the
                # products, not the folio.
                "items": [] if order.vlux_credit_abono else self._statement_items(order),
                "ticket_total": currency.round(order.amount_total),
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
