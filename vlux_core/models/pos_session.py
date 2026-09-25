"""Register closing rules enforced by the server, not only by the screen.

Odoo lets a register limit how far the counted money may differ from the
expected amount (*Set Maximum Difference*), but it enforces that limit only
in the closing popup's JavaScript: ``close_session_from_ui`` closes the
session whatever the difference if it is called directly. A VLUX interface,
a replayed request or a modified client would bypass it.

The same rule is applied here, mirroring the popup: above the limit only a
POS manager may close. With employee login (``pos_hr``) the person who
counts is the session's cashier employee, so that employee's user must be
the manager, not merely whoever holds the browser session.
"""
from odoo import _, models
from odoo.tools import float_compare


class PosSession(models.Model):
    _inherit = "pos.session"

    def _vlux_closing_difference(self, bank_payment_method_diff_pairs):
        """Largest absolute difference the closing would book."""
        self.ensure_one()
        differences = [abs(amount or 0.0) for _method, amount in (bank_payment_method_diff_pairs or [])]
        if self.config_id.cash_control:
            # Odoo computes it without declared dependencies: re-read it, or a
            # count posted earlier in the same transaction would be ignored.
            self.invalidate_recordset(["cash_register_balance_end", "cash_register_difference"])
            differences.append(abs(self.cash_register_difference or 0.0))
        return max(differences, default=0.0)

    def _vlux_closer_is_manager(self):
        self.ensure_one()
        manager_group = "point_of_sale.group_pos_manager"
        employee = self.employee_id if "employee_id" in self._fields else False
        if self.config_id.module_pos_hr and employee:
            return bool(employee.sudo().user_id) and employee.sudo().user_id.has_group(manager_group)
        return self.env.user.has_group(manager_group)

    def _vlux_closing_refusal(self, bank_payment_method_diff_pairs):
        """A ``close_session_from_ui`` refusal, or None when closing is allowed."""
        self.ensure_one()
        config = self.config_id
        if not config.set_maximum_difference:
            return None
        difference = self._vlux_closing_difference(bank_payment_method_diff_pairs)
        rounding = self.currency_id.rounding
        if float_compare(difference, config.amount_authorized_diff, precision_rounding=rounding) <= 0:
            return None
        if self._vlux_closer_is_manager():
            return None
        return {
            "successful": False,
            "redirect": False,
            "message": _(
                "La diferencia del corte (%(difference)s) supera la permitida (%(allowed)s). "
                "Vuelve a contar o pide a un supervisor que cierre la caja.",
                difference=self.currency_id.format(difference),
                allowed=self.currency_id.format(config.amount_authorized_diff),
            ),
        }

    def close_session_from_ui(self, bank_payment_method_diff_pairs=None):
        self.ensure_one()
        refusal = self._vlux_closing_refusal(bank_payment_method_diff_pairs)
        if refusal:
            return refusal
        return super().close_session_from_ui(bank_payment_method_diff_pairs)
