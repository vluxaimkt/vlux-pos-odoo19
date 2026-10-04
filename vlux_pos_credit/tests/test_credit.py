"""Sales on credit through the same server calls the POS makes.

A customer authorised for credit buys, the balance grows; a refund on credit
lowers it; the register's cash is untouched and the closing books the debt on
the customer's receivable. Sales that break a rule (a cashier selling on
credit, a customer without credit, a balance above the limit) are kept, never
refused, and flagged for the owner.
"""
from odoo.exceptions import AccessError, ValidationError
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

    def test_a_store_may_let_any_cashier_sell_on_credit(self):
        session = self.open_new_session(opening_cash=500.0)
        session.config_id.vlux_credit_sellers = "all"
        order = self._sell_on_credit(1, self.cajera)
        self.assertFalse(order.vlux_credit_flagged, "the register's option, not a fixed rule")
        self.assertEqual(session.config_id._vlux_api_register_options()["credit_sellers"], "all")

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

    def test_the_encargado_authorises_credit_from_the_register(self):
        partner = self.env["res.partner"].create({"name": "Cliente Nuevo"})
        status = self.env["res.partner"].vlux_pos_set_credit(
            partner.id, True, 500, self.config.id, self.encargado.id
        )
        self.assertEqual(status, {"allowed": True, "limit": 500.0, "balance": 0.0})
        self.assertTrue(partner.vlux_credit_allowed)
        self.assertEqual(partner.vlux_credit_limit, 500.0)
        self.assertIn("Encargado Crédito", partner.sudo().message_ids[:1].body, "who did it is on record")

        # Withdrawing credit keeps the limit on file.
        self.env["res.partner"].vlux_pos_set_credit(partner.id, False, 0, self.config.id, self.encargado.id)
        self.assertFalse(partner.vlux_credit_allowed)
        self.assertEqual(partner.vlux_credit_limit, 500.0)

    def test_a_cashier_cannot_authorise_credit(self):
        partner = self.env["res.partner"].create({"name": "Cliente Nuevo"})
        with self.assertRaises(AccessError):
            self.env["res.partner"].vlux_pos_set_credit(partner.id, True, 500, self.config.id, self.cajera.id)
        with self.assertRaises(AccessError):
            self.env["res.partner"].vlux_pos_set_credit(partner.id, True, 500, self.config.id, False)
        self.assertFalse(partner.vlux_credit_allowed)

    def test_the_limit_must_make_sense(self):
        partner = self.env["res.partner"].create({"name": "Cliente Nuevo"})
        for bad in (-1, "mil", 20_000_000):
            with self.assertRaises(ValidationError):
                self.env["res.partner"].vlux_pos_set_credit(partner.id, True, bad, self.config.id, self.encargado.id)

    # ------------------------------------------------------------------
    # abonos
    # ------------------------------------------------------------------

    def _abono(self, session, amount, method=None, uuid="abono-1", employee=None):
        return self.env["pos.session"].vlux_pos_register_abono(
            session.id, self.customer.id, amount, (method or self.cash_pm1).id, uuid, (employee or self.cajera).id
        )

    def test_a_cash_abono_lowers_the_debt_and_enters_the_drawer(self):
        session = self.open_new_session(opening_cash=500.0)
        self._sell_on_credit(2, self.encargado)  # owes 36

        ticket = self._abono(session, 20.0)

        self.assertAlmostEqual(ticket["previous_balance"], 36.0)
        self.assertAlmostEqual(ticket["amount"], 20.0)
        self.assertAlmostEqual(ticket["new_balance"], 16.0)
        self.assertEqual(ticket["cashier"], "Cajera Crédito", "any cashier may receive an abono")
        self.customer.invalidate_recordset(["vlux_credit_balance"])
        self.assertAlmostEqual(self.customer.vlux_credit_balance, 16.0)
        abono = self.env["pos.order"].browse(ticket["order_id"])
        self.assertTrue(abono.vlux_credit_abono)
        self.assertFalse(abono.lines)
        self.assertFalse(abono.vlux_credit_flagged)

        # The 20 are in the drawer: 500 float + 20.
        closing = session.get_closing_control_data()
        self.assertAlmostEqual(closing["default_cash_details"]["amount"], 520.0)
        session.post_closing_cash_details(520.0)
        session.update_closing_control_state_session("")
        result = session.close_session_from_ui()
        self.assertTrue(result["successful"], result)
        self.assertAlmostEqual(sum(session.move_id.line_ids.mapped("balance")), 0.0)
        receivable = self.env["account.move.line"].search([
            ("partner_id", "=", self.customer.id),
            ("account_id", "=", self.customer.property_account_receivable_id.id),
            ("parent_state", "=", "posted"),
        ])
        self.assertAlmostEqual(sum(receivable.mapped("balance")), 16.0, msg="the books agree: 36 - 20")

    def test_a_card_abono_does_not_touch_the_drawer(self):
        session = self.open_new_session(opening_cash=500.0)
        self._sell_on_credit(2, self.encargado)
        ticket = self._abono(session, 36.0, method=self.bank_pm1)

        self.assertAlmostEqual(ticket["new_balance"], 0.0)
        self.assertEqual(ticket["method"], self.bank_pm1.name)
        self.assertAlmostEqual(session.get_closing_control_data()["default_cash_details"]["amount"], 500.0)

    def test_a_retried_abono_is_registered_once(self):
        session = self.open_new_session()
        self._sell_on_credit(2, self.encargado)
        first = self._abono(session, 10.0, uuid="retry-me")
        again = self._abono(session, 10.0, uuid="retry-me")

        self.assertEqual(first["order_id"], again["order_id"])
        self.assertEqual(self.env["pos.order"].search_count([("vlux_credit_abono", "=", True)]), 1)
        self.customer.invalidate_recordset(["vlux_credit_balance"])
        self.assertAlmostEqual(self.customer.vlux_credit_balance, 26.0)

    def test_an_abono_cannot_exceed_the_debt_or_be_on_credit(self):
        session = self.open_new_session()
        self._sell_on_credit(2, self.encargado)  # owes 36
        with self.assertRaises(ValidationError):
            self._abono(session, 40.0, uuid="too-much")
        with self.assertRaises(ValidationError):
            self._abono(session, 0.0, uuid="zero")
        with self.assertRaises(ValidationError):
            self._abono(session, 10.0, method=self.pay_later_pm, uuid="on-credit")
        self.assertFalse(self.env["pos.order"].search([("vlux_credit_abono", "=", True)]))

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
