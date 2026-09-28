from odoo import api, fields, models

from .pos_payment_method import CREDIT_ORDER_STATES


class ResPartner(models.Model):
    _inherit = "res.partner"

    # A store decides who may buy on credit and up to how much; the balance is
    # what the customer owes from POS sales on credit minus what was paid back.
    vlux_credit_allowed = fields.Boolean(
        string="Crédito autorizado",
        tracking=True,
        help="Sólo los clientes autorizados pueden comprar a crédito en el POS.",
    )
    vlux_credit_limit = fields.Monetary(
        string="Límite de crédito",
        currency_field="vlux_credit_currency_id",
        tracking=True,
        help="Saldo máximo que el cliente puede deber. 0 = sin límite.",
    )
    vlux_credit_balance = fields.Monetary(
        string="Saldo a crédito",
        currency_field="vlux_credit_currency_id",
        compute="_compute_vlux_credit_balance",
        help="Lo que el cliente debe por ventas a crédito del POS, menos sus abonos.",
    )
    vlux_credit_currency_id = fields.Many2one(
        "res.currency", compute="_compute_vlux_credit_currency_id",
    )

    def _compute_vlux_credit_currency_id(self):
        for partner in self:
            partner.vlux_credit_currency_id = (partner.company_id or self.env.company).currency_id

    def _compute_vlux_credit_balance(self):
        balances = self._vlux_credit_balances()
        for partner in self:
            partner.vlux_credit_balance = balances.get(partner.id, 0.0)

    def _vlux_credit_balances(self):
        """``{partner_id: balance}`` from POS payments on customer account.

        A sale on credit is a positive customer-account payment, a refund or an
        abono a negative one; the balance is their sum. Orders still being
        edited on a register (draft) do not count yet.
        """
        partners = self.filtered("id")
        if not partners:
            return {}
        groups = self.env["pos.payment"].sudo()._read_group(
            [
                # Odoo's customer account: a payment method without journal.
                ("payment_method_id.journal_id", "=", False),
                ("pos_order_id.partner_id", "in", partners.ids),
                ("pos_order_id.state", "in", CREDIT_ORDER_STATES),
            ],
            groupby=["pos_order_id"],
            aggregates=["amount:sum"],
        )
        balances = {}
        for order, amount in groups:
            partner_id = order.partner_id.id
            balances[partner_id] = balances.get(partner_id, 0.0) + amount
        return balances

    def vlux_credit_available(self):
        """What is left of the limit (None when the partner has no limit)."""
        self.ensure_one()
        if not self.vlux_credit_limit:
            return None
        return self.vlux_credit_limit - self.vlux_credit_balance

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + [
            "vlux_credit_allowed",
            "vlux_credit_limit",
            "vlux_credit_balance",
        ]
