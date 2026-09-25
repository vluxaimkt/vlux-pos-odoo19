"""A full register day, through the same server calls the POS screen makes.

Opening with a float, cash and card sales, a cash withdrawal, a partial
refund paid back in cash, the closing count with a difference, and the
session report. Every amount is checked against what a cashier would count
by hand, so a change in Odoo's closing logic that alters the money shows up
here before it reaches a store.
"""
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon

CASH_IN_OUT_EXTRAS = {"formattedAmount": "", "translatedType": "out"}


@tagged("post_install", "-at_install")
class TestVluxRegisterDay(TestPoSCommon):

    def setUp(self):
        super().setUp()
        self.config = self.basic_config
        self.soda = self.create_product("Refresco 600 ml", self.categ_basic, 18.0, 10.0)
        self.chips = self.create_product("Papas 45 g", self.categ_basic, 20.0, 12.0)
        self.adjust_inventory([self.soda, self.chips], [50, 50])

    def _synced(self, data):
        # sync_from_ui also returns the orders a refund points to: pick ours by uuid.
        self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _sell(self, lines, payments=None, **order_values):
        return self._synced(self.create_ui_order_data(lines, pos_order_ui_args=order_values, payments=payments))

    def _refund(self, order, line, quantity, payment_method):
        amount = -line.price_unit * quantity
        data = self.create_ui_order_data(
            [{"product": line.product_id, "quantity": -quantity, "refunded_orderline_id": line.id}],
            payments=[(payment_method, amount)],
        )
        return self._synced(data)

    def _open_day(self):
        session = self.open_new_session(opening_cash=500.0)
        # Cash sale: 2 sodas + 1 chips = 56. Card sale: 3 chips = 60. Cash sale: 1 soda = 18.
        first = self._sell([(self.soda, 2), (self.chips, 1)])
        self._sell([(self.chips, 3)], payments=[(self.bank_pm1, 60.0)])
        self._sell([(self.soda, 1)])
        session.try_cash_in_out("out", 100.0, "Pago a proveedor de hielo", False, CASH_IN_OUT_EXTRAS)
        soda_line = first.lines.filtered(lambda line: line.product_id == self.soda)
        refund = self._refund(first, soda_line, 1, self.cash_pm1)
        return session, first, refund

    def test_a_register_day_adds_up(self):
        session, first, refund = self._open_day()

        # What the cashier must have in the drawer: 500 float + 56 + 18 cash
        # sales - 100 withdrawal - 18 refund = 456.
        closing = session.get_closing_control_data()
        self.assertAlmostEqual(closing["default_cash_details"]["amount"], 456.0)
        self.assertAlmostEqual(closing["default_cash_details"]["opening"], 500.0)
        self.assertEqual(len(closing["default_cash_details"]["moves"]), 1)

        # The refund is tied to the sale it reverses and cannot exceed it.
        self.assertEqual(refund.refunded_order_id, first)
        self.assertAlmostEqual(refund.amount_total, -18.0)
        soda_line = first.lines.filtered(lambda line: line.product_id == self.soda)
        self.assertEqual(soda_line.refunded_qty, 1)

        # Counted 452: 4 short. Without a limit the closing books the difference.
        session.post_closing_cash_details(452.0)
        session.update_closing_control_state_session("Faltaron 4 pesos")
        result = session.close_session_from_ui()
        self.assertTrue(result["successful"], result)
        self.assertEqual(session.state, "closed")
        self.assertAlmostEqual(session.cash_register_difference, -4.0)
        self.assertEqual(session.move_id.state, "posted")
        self.assertAlmostEqual(sum(session.move_id.line_ids.mapped("balance")), 0.0)

        # Stock: 3 sodas sold, 1 returned; 4 chips sold.
        self.assertEqual(self.soda.qty_available, 48)
        self.assertEqual(self.chips.qty_available, 46)

        # The session report (the "Z") carries the same totals.
        report = self.env["report.point_of_sale.report_saledetails"].get_sale_details(session_ids=session.ids)
        payments = {}
        for payment in report["payments"]:
            payments[payment.get("id")] = payments.get(payment.get("id"), 0.0) + payment["total"]
        self.assertAlmostEqual(payments[self.cash_pm1.id], 56.0 + 18.0 - 18.0)
        self.assertAlmostEqual(payments[self.bank_pm1.id], 60.0)
        self.assertAlmostEqual(report["currency"]["total_paid"], 116.0)
        self.assertEqual(report["nbr_orders"], 4, "3 sales and 1 refund")

    def test_the_difference_limit_is_enforced_by_the_server(self):
        self.config.write({"set_maximum_difference": True, "amount_authorized_diff": 2.0})
        cashier = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Cajera corte", "login": "vlux-cashier-closing",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("point_of_sale.group_pos_user").id])],
        })
        session, _first, _refund = self._open_day()
        session.post_closing_cash_details(452.0)  # 4 short, limit 2

        refused = session.with_user(cashier).close_session_from_ui()
        self.assertFalse(refused["successful"])
        self.assertIn("supera la permitida", refused["message"])
        self.assertNotEqual(session.state, "closed")

        # A card difference counts too.
        session.post_closing_cash_details(456.0)
        refused = session.with_user(cashier).close_session_from_ui([(self.bank_pm1.id, -5.0)])
        self.assertFalse(refused["successful"])

        # Within the limit the cashier closes.
        session.post_closing_cash_details(455.0)
        result = session.with_user(cashier).close_session_from_ui()
        self.assertTrue(result["successful"], result)
        self.assertEqual(session.state, "closed")

    def test_a_manager_may_close_above_the_limit(self):
        self.config.write({"set_maximum_difference": True, "amount_authorized_diff": 2.0})
        session, _first, _refund = self._open_day()
        session.post_closing_cash_details(400.0)  # the test user is a POS manager
        result = session.close_session_from_ui()
        self.assertTrue(result["successful"], result)
        self.assertAlmostEqual(session.cash_register_difference, -56.0)

    def test_only_managers_move_cash(self):
        cashier = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Cajero retiro", "login": "vlux-cashier-cash-out",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("point_of_sale.group_pos_user").id])],
        })
        session = self.open_new_session(opening_cash=100.0)
        with self.assertRaises(AccessError):
            session.with_user(cashier).try_cash_in_out("out", 50.0, "Retiro", False, CASH_IN_OUT_EXTRAS)
