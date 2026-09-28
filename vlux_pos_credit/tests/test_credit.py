"""Sales on credit through the same server calls the POS makes.

A customer authorised for credit buys, the balance grows; a refund on credit
lowers it; the register's cash is untouched and the closing books the debt on
the customer's receivable. Sales that break a rule (a cashier selling on
credit, a customer without credit, a balance above the limit) are kept, never
refused, and flagged for the owner.
"""
from odoo.tests.common import tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from ..models.pos_order import ISSUE_NOT_AUTHORIZED, ISSUE_NOT_MANAGER, ISSUE_OVER_LIMIT
from ..models.pos_payment_method import CREDIT_METHOD_NAME


@tagged("post_install", "-at_install")
class TestVluxCredit(TestPoSCommon):

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        self.soda = self.create_product("Refresco 600 ml", self.categ_basic, 18.0, 10.0)
        self.adjust_inventory([self.soda], [50])
        Employee = self.env["hr.employee"].sudo()
        self.encargado = Employee.create({"name": "Encargado Crédito", "pin": "1357"})
        self.cajera = Employee.create({"name": "Cajera Crédito", "pin": "2468"})
        self.config.write({
            "module_pos_hr": True,
            "advanced_employee_ids": [(4, self.encargado.id)],
            "basic_employee_ids": [(4, self.cajera.id)],
        })
        self.customer.write({"vlux_credit_allowed": True, "vlux_credit_limit": 1000.0})

    def _synced(self, data):
        self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _sell_on_credit(self, quantity, employee, customer=None, amount=None):
        amount = 18.0 * quantity if amount is None else amount
        data = self.create_ui_order_data(
            [(self.soda, quantity)],
            pos_order_ui_args={"employee_id": employee.id},
            customer=customer or self.customer,
            payments=[(self.pay_later_pm, amount)],
        )
        return self._synced(data)

    def test_a_sale_on_credit_by_the_encargado_raises_the_balance(self):
        self.open_new_session(opening_cash=500.0)
        order = self._sell_on_credit(2, self.encargado)

        self.assertEqual(order.state, "paid")
        self.assertAlmostEqual(order.vlux_credit_amount, 36.0)
        self.assertFalse(order.vlux_credit_flagged)
        self.assertAlmostEqual(self.customer.vlux_credit_balance, 36.0)
        self.assertAlmostEqual(self.customer.vlux_credit_available(), 964.0)

    def test_a_cashier_sale_on_credit_is_kept_and_flagged(self):
        self.open_new_session()
        order = self._sell_on_credit(1, self.cajera)

        self.assertEqual(order.state, "paid", "a synced sale is never refused")
        self.assertTrue(order.vlux_credit_flagged)
        self.assertIn(ISSUE_NOT_MANAGER, order.vlux_credit_issues)
        self.assertAlmostEqual(self.customer.vlux_credit_balance, 18.0, msg="the debt still counts")

    def test_a_customer_without_credit_is_flagged(self):
        self.open_new_session()
        self.other_customer.vlux_credit_allowed = False
        order = self._sell_on_credit(1, self.encargado, customer=self.other_customer)

        self.assertTrue(order.vlux_credit_flagged)
        self.assertIn(ISSUE_NOT_AUTHORIZED, order.vlux_credit_issues)
        self.assertNotIn(ISSUE_NOT_MANAGER, order.vlux_credit_issues)

    def test_going_over_the_limit_is_flagged(self):
        self.customer.vlux_credit_limit = 30.0
        self.open_new_session()
        order = self._sell_on_credit(2, self.encargado)  # 36 > 30

        self.assertTrue(order.vlux_credit_flagged)
        self.assertIn(ISSUE_OVER_LIMIT, order.vlux_credit_issues)
        self.assertAlmostEqual(self.customer.vlux_credit_available(), -6.0)

    def test_a_refund_on_credit_lowers_the_balance(self):
        self.open_new_session()
        sale = self._sell_on_credit(2, self.encargado)
        line = sale.lines
        refund = self._synced(self.create_ui_order_data(
            [{"product": self.soda, "quantity": -1, "refunded_orderline_id": line.id}],
            pos_order_ui_args={"employee_id": self.cajera.id},
            customer=self.customer,
            payments=[(self.pay_later_pm, -18.0)],
        ))

        self.assertAlmostEqual(refund.vlux_credit_amount, -18.0)
        self.assertFalse(refund.vlux_credit_flagged, "lowering a debt breaks no rule, whoever does it")
        self.customer.invalidate_recordset(["vlux_credit_balance"])
        self.assertAlmostEqual(self.customer.vlux_credit_balance, 18.0)

    def test_credit_is_not_drawer_cash_and_the_closing_books_the_debt(self):
        session = self.open_new_session(opening_cash=500.0)
        self._sell_on_credit(2, self.encargado)
        self._synced(self.create_ui_order_data([(self.soda, 1)], pos_order_ui_args={"employee_id": self.cajera.id}))

        # 500 float + 18 cash sale; the 36 on credit never entered the drawer.
        closing = session.get_closing_control_data()
        self.assertAlmostEqual(closing["default_cash_details"]["amount"], 518.0)

        session.post_closing_cash_details(518.0)
        session.update_closing_control_state_session("")
        result = session.close_session_from_ui()
        self.assertTrue(result["successful"], result)
        self.assertEqual(session.move_id.state, "posted")
        self.assertAlmostEqual(sum(session.move_id.line_ids.mapped("balance")), 0.0)

        receivable = self.env["account.move.line"].search([
            ("partner_id", "=", self.customer.id),
            ("account_id", "=", self.customer.property_account_receivable_id.id),
            ("parent_state", "=", "posted"),
        ])
        self.assertAlmostEqual(sum(receivable.mapped("balance")), 36.0, msg="the customer owes it in the books")
        self.assertAlmostEqual(self.customer.vlux_credit_balance, 36.0, msg="and the POS balance survives the closing")

    def test_orders_synced_together_each_see_the_previous_balance(self):
        self.customer.vlux_credit_limit = 50.0
        self.open_new_session()
        first = self.create_ui_order_data(
            [(self.soda, 2)], pos_order_ui_args={"employee_id": self.encargado.id},
            customer=self.customer, payments=[(self.pay_later_pm, 36.0)],
        )
        second = self.create_ui_order_data(
            [(self.soda, 1)], pos_order_ui_args={"employee_id": self.encargado.id},
            customer=self.customer, payments=[(self.pay_later_pm, 18.0)],
        )
        # A register back online sends its pending orders in one call.
        self.env["pos.order"].sync_from_ui([first, second])
        orders = self.env["pos.order"].search([("uuid", "in", [first["uuid"], second["uuid"]])])
        flagged = orders.filtered("vlux_credit_flagged")
        self.assertEqual(len(flagged), 1, "only the order that crossed 50 is flagged")
        self.assertAlmostEqual(flagged.vlux_credit_amount, 18.0)

    def test_credit_fields_reach_the_register(self):
        fields = self.env["res.partner"]._load_pos_data_fields(self.config)
        for name in ("vlux_credit_allowed", "vlux_credit_limit", "vlux_credit_balance"):
            self.assertIn(name, fields)

    def test_the_credit_method_is_created_once(self):
        Method = self.env["pos.payment.method"]
        method = Method._vlux_credit_method()
        self.assertEqual(method.name, CREDIT_METHOD_NAME)
        self.assertEqual(method.type, "pay_later")
        self.assertTrue(method.split_transactions, "the register must ask for the customer")
        self.assertEqual(Method._vlux_credit_method(), method)
