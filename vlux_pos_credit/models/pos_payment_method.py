from odoo import _, api, models
from odoo.exceptions import UserError

CREDIT_METHOD_NAME = "Crédito"
# Orders that count towards a customer's balance: synced and paid, whether or
# not the session is closed (done) or the order was invoiced.
CREDIT_ORDER_STATES = ("paid", "done", "invoiced")


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    @api.model
    def _vlux_credit_method(self, company=None):
        """The store's "Crédito" payment method, created on first use.

        Odoo's customer account ("pay_later": no journal) with split
        transactions, so the register asks for the customer and each sale on
        credit lands on that customer's receivable when the session closes.
        """
        company = company or self.env.company
        method = self.sudo().search([
            ("company_id", "=", company.id),
            ("journal_id", "=", False),
            ("name", "=", CREDIT_METHOD_NAME),
        ], limit=1)
        if not method:
            method = self.sudo().create({
                "name": CREDIT_METHOD_NAME,
                "company_id": company.id,
                "split_transactions": True,
            })
        if not method.split_transactions:
            raise UserError(_("La forma de pago Crédito debe pedir el cliente (transacciones divididas)."))
        return method
