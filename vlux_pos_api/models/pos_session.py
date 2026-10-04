from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    # The id the register gave a cash move: sending it twice records it once.
    vlux_api_uuid = fields.Char(string="VLUX API uuid", index=True, copy=False, readonly=True)

    _vlux_api_uuid_unique = models.Constraint("unique (vlux_api_uuid)", "A cash move with this uuid already exists")


class PosSession(models.Model):
    _inherit = "pos.session"

    def _vlux_api_closing_extra(self):
        """More sections for GET /registers/<id>/session/closing (hook; none by default)."""
        self.ensure_one()
        return {}

    def _vlux_api_cash_move(self, employee, kind, amount, reason, uuid):
        """Put cash in or take it out of the drawer, as the Odoo POS "Entrada/Salida de efectivo".

        Same statement line as ``try_cash_in_out`` (so the closing expects it
        and the accounting is the POS's), with the employee who did it.
        Returns ``(line, duplicate)``: a uuid already recorded is not recorded again.
        """
        self.ensure_one()
        Line = self.env["account.bank.statement.line"].sudo()
        existing = Line.search([("vlux_api_uuid", "=", uuid)], limit=1)
        if existing:
            if existing.pos_session_id != self:
                raise ValidationError(_("Ese uuid ya se usó en otra sesión."))
            return existing, True
        if self.state != "opened":
            raise UserError(_("La caja no está abierta."))
        if not self.cash_journal_id:
            raise UserError(_("Esta caja no maneja efectivo."))
        sign = 1 if kind == "in" else -1
        label = _("Entrada") if kind == "in" else _("Salida")
        partner = employee.sudo().work_contact_id if employee else self.env.user.partner_id
        vals = self._prepare_account_bank_statement_line_vals(
            self, sign, amount, reason, partner.id or False,
            {"translatedType": label, **({"employee_id": employee.id} if employee else {})},
        )
        vals["vlux_api_uuid"] = uuid
        return Line.with_context(no_retrieve_partner=True).create(vals), False

    def _vlux_api_cash_moves(self):
        """The session's cash moves, oldest first, with who made them."""
        self.ensure_one()
        return [
            {
                "id": line.id,
                "type": "in" if line.amount > 0 else "out",
                "amount": abs(line.amount),
                "name": line.payment_ref or "",
                "employee": (line.employee_id or line.partner_id).name or None,
                "date": fields.Datetime.to_string(line.create_date) + "Z" if line.create_date else None,
            }
            for line in self.sudo().statement_line_ids.sorted("create_date")
        ]
