from odoo import models

MANAGER_GROUP = "point_of_sale.group_pos_manager"


class PosConfig(models.Model):
    _inherit = "pos.config"

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
