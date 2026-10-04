from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

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
        help="Lo que el cliente debe por ventas a crédito del POS y saldos iniciales, menos sus abonos.",
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
        # What they already owed before (the store's notebook).
        for opening in self.env["vlux.credit.opening"]._vlux_posted(partners.ids):
            partner_id = opening.partner_id.id
            balances[partner_id] = balances.get(partner_id, 0.0) + opening.amount
        return balances

    def vlux_credit_available(self):
        """What is left of the limit (None when the partner has no limit)."""
        self.ensure_one()
        if not self.vlux_credit_limit:
            return None
        return self.vlux_credit_limit - self.vlux_credit_balance

    @api.model
    def vlux_pos_credit_status(self, partner_id):
        """Credit of one customer as the register needs it, fresh from the server."""
        partner = self.browse(int(partner_id)).exists()
        if not partner:
            return {"allowed": False, "limit": 0.0, "balance": 0.0}
        partner.check_access("read")
        partner = partner.sudo()
        return {
            "allowed": partner.vlux_credit_allowed,
            "limit": partner.vlux_credit_limit,
            "balance": partner.vlux_credit_balance,
        }

    @api.model
    def vlux_pos_set_credit(self, partner_id, allowed, limit, config_id, employee_id=False):
        """Authorise (or not) a customer for credit and set the limit, from the register.

        Only the encargado or the owner may (decision D7): the register sends
        who is logged in and the server checks it against the register.
        """
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(_("Se requiere un usuario de punto de venta."))
        config = self.env["pos.config"].browse(int(config_id)).exists()
        if not config:
            raise AccessError(_("La caja no existe."))
        config.check_access("read")
        employee = self.env["hr.employee"].sudo().browse(int(employee_id)).exists() if employee_id else None
        if employee and employee.company_id and employee.company_id != config.company_id:
            raise AccessError(_("El empleado no pertenece a esta tienda."))
        if not config._vlux_is_manager(employee):
            raise AccessError(_("Sólo el encargado o el dueño pueden autorizar crédito."))
        try:
            limit = float(limit or 0.0)
        except (TypeError, ValueError):
            raise ValidationError(_("El límite de crédito debe ser un número."))
        if limit < 0 or limit > 10_000_000:
            raise ValidationError(_("El límite de crédito debe estar entre 0 y 10,000,000."))
        partner = self.browse(int(partner_id)).exists()
        if not partner:
            raise ValidationError(_("El cliente no existe."))
        partner.check_access("read")
        who = employee.name if employee else self.env.user.name
        partner.sudo().with_context(mail_notrack=False).write({
            "vlux_credit_allowed": bool(allowed),
            "vlux_credit_limit": limit if allowed else partner.sudo().vlux_credit_limit,
        })
        partner.sudo().message_post(body=_(
            "Crédito %(state)s desde la caja %(register)s por %(who)s. Límite: %(limit)s.",
            state=_("autorizado") if allowed else _("retirado"),
            register=config.name,
            who=who,
            limit=partner.vlux_credit_currency_id.format(partner.sudo().vlux_credit_limit),
        ))
        return self.vlux_pos_credit_status(partner.id)

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + [
            "vlux_credit_allowed",
            "vlux_credit_limit",
            "vlux_credit_balance",
        ]
