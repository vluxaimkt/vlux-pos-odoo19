from odoo import fields, models

MANAGER_GROUP = "point_of_sale.group_pos_manager"


class PosConfig(models.Model):
    _inherit = "pos.config"

    # Who may sell on credit is each store's decision (D7 was one store's rule).
    vlux_credit_sellers = fields.Selection(
        [("managers", "Sólo el encargado y el dueño"), ("all", "Cualquier cajero")],
        string="Quién puede vender a crédito",
        default="managers", required=True,
        help="Autorizar crédito y fijar límites sigue siendo del encargado o el dueño.",
    )

    def _vlux_may_sell_on_credit(self, employee=None, user=None):
        """Whether the person at this register may sell on credit under the register's option."""
        self.ensure_one()
        return self.vlux_credit_sellers == "all" or self._vlux_is_manager(employee, user)

    def _vlux_api_owner_sections(self):
        return [*super()._vlux_api_owner_sections(), "credit"]

    def _vlux_api_option_fields(self):
        return [*super()._vlux_api_option_fields(), "vlux_credit_sellers"]

    def _vlux_api_register_options(self):
        options = super()._vlux_api_register_options()
        options["credit_sellers"] = self.vlux_credit_sellers
        return options

    def _vlux_is_manager(self, employee=None, user=None):
        """Whether the person at this register is the encargado or the owner.

        With employee login (pos_hr) that is the employee: a manager of the
        register (Odoo's "advanced" list) or linked to a POS manager user. The
        register reports who is logged in; the browser session alone is not
        enough, since the owner's Odoo user is often the one logged in while a
        cashier works. Without employee login, the user.
        """
        self.ensure_one()
        employee = employee.sudo() if employee else employee
        if self.module_pos_hr:
            if not employee:
                return False
            return employee in self.advanced_employee_ids or bool(
                employee.user_id and employee.user_id.has_group(MANAGER_GROUP)
            )
        user = user or self.env.user
        return bool(user and user.has_group(MANAGER_GROUP))
