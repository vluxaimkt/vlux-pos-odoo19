"""Server-side rules for sales on credit.

The register hides the Crédito payment method from cashiers and offers only
authorised customers, but the server decides what counts. It never refuses a
synced order (the goods already left the store, maybe while offline); an order
that breaks a rule is flagged so the owner sees it.
"""
from odoo import api, fields, models
from odoo.tools import float_compare

ISSUE_NOT_MANAGER = "Vendida a crédito por alguien que no es encargado ni dueño"
ISSUE_NOT_AUTHORIZED = "El cliente no tiene crédito autorizado"
ISSUE_OVER_LIMIT = "El saldo del cliente rebasó su límite de crédito"
ISSUE_NO_CUSTOMER = "Venta a crédito sin cliente"


class PosOrder(models.Model):
    _inherit = "pos.order"

    vlux_credit_amount = fields.Monetary(
        string="A crédito",
        compute="_compute_vlux_credit_amount",
        store=True,
        help="Parte del ticket pagada con la forma de pago Crédito.",
    )
    vlux_credit_prev_balance = fields.Monetary(
        string="Saldo anterior (ticket)",
        help="Saldo del cliente que la caja imprimió como 'saldo anterior'. Sólo para el "
        "ticket: el saldo real se calcula de los pagos.",
    )
    vlux_credit_flagged = fields.Boolean(string="Crédito por revisar", readonly=True, index=True)
    vlux_credit_issues = fields.Text(string="Motivo de revisión (crédito)", readonly=True)

    @api.depends("payment_ids.amount", "payment_ids.payment_method_id")
    def _compute_vlux_credit_amount(self):
        for order in self:
            order.vlux_credit_amount = sum(
                payment.amount
                for payment in order.payment_ids
                if payment.payment_method_id.type == "pay_later"
            )

    def _vlux_sold_by_manager(self):
        """Whether the encargado or the owner was at the register."""
        self.ensure_one()
        return self.session_id.config_id._vlux_is_manager(self.employee_id, self.user_id)

    def _vlux_credit_issues(self):
        """Rules a sale on credit broke (empty list when none)."""
        self.ensure_one()
        rounding = self.currency_id.rounding
        if float_compare(self.vlux_credit_amount, 0.0, precision_rounding=rounding) <= 0:
            # Refunds and abonos lower a balance: nothing to check.
            return []
        issues = []
        if not self._vlux_sold_by_manager():
            issues.append(ISSUE_NOT_MANAGER)
        partner = self.partner_id
        if not partner:
            issues.append(ISSUE_NO_CUSTOMER)
            return issues
        if not partner.vlux_credit_allowed:
            issues.append(ISSUE_NOT_AUTHORIZED)
        # The balance is computed, not stored: several orders synced in one
        # batch (a register coming back online) must each see the previous.
        partner.invalidate_recordset(["vlux_credit_balance"])
        limit = partner.vlux_credit_limit
        if limit and float_compare(partner.vlux_credit_balance, limit, precision_rounding=rounding) > 0:
            issues.append(ISSUE_OVER_LIMIT)
        return issues

    def _vlux_check_credit(self):
        for order in self.filtered(lambda o: o.state in ("paid", "done", "invoiced")):
            issues = order._vlux_credit_issues()
            if issues and not order.vlux_credit_flagged:
                order.sudo().write({"vlux_credit_flagged": True, "vlux_credit_issues": "\n".join(issues)})

    def _process_order(self, order, existing_order):
        order_id = super()._process_order(order, existing_order)
        self.browse(order_id)._vlux_check_credit()
        return order_id
