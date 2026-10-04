"""What customers already owed before the store started selling with VLUX (the notebook).

A posted opening is a journal entry: the customer's receivable against the
company's opening-balance account (undistributed earnings), as Odoo records
opening balances. It is not a sale: today's sales and income do not change.
Abonos made later at the register settle it like any other debt, and the
balance the register, the owner panel and the statement show includes it.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError



class VluxCreditOpening(models.Model):
    _name = "vlux.credit.opening"
    _description = "Saldo inicial de crédito (libreta)"
    _order = "date desc, id desc"

    partner_id = fields.Many2one("res.partner", string="Cliente", required=True, index=True,
                                 domain="[('is_company', '=', False)]")
    company_id = fields.Many2one("res.company", string="Empresa", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    amount = fields.Monetary(string="Debe", required=True, currency_field="currency_id")
    date = fields.Date(string="Fecha", required=True, default=fields.Date.context_today,
                       help="Desde cuándo debe (la fecha de la libreta).")
    note = fields.Char(string="Nota", help="Por ejemplo: libreta de 2026, hoja 12.")
    allow_credit = fields.Boolean(string="Autorizar crédito", default=True,
                                  help="Al registrar, deja al cliente con crédito autorizado.")
    credit_limit = fields.Monetary(string="Límite", currency_field="currency_id",
                                   help="Límite de crédito a fijar al registrar (0 = no cambiarlo).")
    state = fields.Selection([("draft", "Borrador"), ("posted", "Registrado"), ("cancel", "Cancelado")],
                             string="Estado", default="draft", required=True, readonly=True)
    move_id = fields.Many2one("account.move", string="Asiento", readonly=True, copy=False)

    @api.constrains("amount")
    def _check_amount(self):
        for opening in self:
            if opening.amount <= 0:
                raise ValidationError(_("El saldo inicial debe ser mayor que cero."))

    def write(self, vals):
        if any(opening.state != "draft" for opening in self) and set(vals) - {"note"}:
            raise UserError(_("Un saldo inicial registrado no se modifica: cancélalo y registra otro."))
        return super().write(vals)

    def unlink(self):
        if any(opening.state != "draft" for opening in self):
            raise UserError(_("Un saldo inicial registrado no se borra: cancélalo."))
        return super().unlink()

    def _journal(self):
        journal = self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", self.company_id.id)], limit=1,
        )
        if not journal:
            raise UserError(_("La empresa no tiene un diario de operaciones varias."))
        return journal

    def action_post(self):
        for opening in self.filtered(lambda o: o.state == "draft"):
            company = opening.company_id
            partner = opening.partner_id.with_company(company)
            receivable = partner.property_account_receivable_id
            if not receivable:
                raise UserError(_("El cliente %s no tiene cuenta por cobrar.", partner.display_name))
            label = _("Saldo inicial de crédito (libreta)")
            if opening.note:
                label = "%s: %s" % (label, opening.note)
            move = self.env["account.move"].sudo().with_company(company).create({
                "move_type": "entry",
                "journal_id": opening._journal().id,
                "date": opening.date,
                "ref": label,
                "line_ids": [
                    fields.Command.create({"name": label, "account_id": receivable.id,
                                           "partner_id": partner.id, "debit": opening.amount}),
                    fields.Command.create({"name": label, "account_id": company.get_unaffected_earnings_account().id,
                                           "partner_id": partner.id, "credit": opening.amount}),
                ],
            })
            move.action_post()
            super(VluxCreditOpening, opening).write({"state": "posted", "move_id": move.id})
            if opening.allow_credit:
                values = {"vlux_credit_allowed": True}
                if opening.credit_limit:
                    values["vlux_credit_limit"] = opening.credit_limit
                partner.sudo().write(values)
        return True

    def action_cancel(self):
        for opening in self.filtered(lambda o: o.state == "posted"):
            # The owner need not have accounting rights: the entry is the module's own.
            move = opening.sudo().move_id
            if move:
                move._reverse_moves(
                    [{"date": fields.Date.context_today(self), "ref": _("Cancelación: %s", move.ref)}],
                    cancel=True,
                )
            super(VluxCreditOpening, opening).write({"state": "cancel"})
        return True

    @api.model
    def _vlux_posted(self, partner_ids):
        return self.sudo().search([("partner_id", "in", list(partner_ids)), ("state", "=", "posted")], order="date, id")
