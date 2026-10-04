import json

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import HttpCase, new_test_user, tagged

from .test_credit import TestVluxCredit


@tagged("post_install", "-at_install")
class TestVluxCreditReport(TestVluxCredit):
    """The owner's credit views: balances and a customer's statement."""

    def setUp(self):
        super().setUp()
        self.owner = new_test_user(
            self.env, login="vlux-credit-owner", name="Dueño",
            groups="base.group_user,point_of_sale.group_pos_user,vlux_core.group_vlux_owner",
        )

    def _report(self, user=None):
        return self.env["vlux.credit.report"].with_user(user or self.owner)

    def test_the_owner_sees_who_owes_and_each_statement(self):
        session = self.open_new_session(opening_cash=500.0)
        self._sell_on_credit(2, self.encargado)  # 36
        self._sell_on_credit(1, self.cajera)  # 18, flagged: a cashier
        self.env["pos.session"].vlux_pos_register_abono(
            session.id, self.customer.id, 20.0, self.cash_pm1.id, "report-abono", self.cajera.id
        )

        balances = self._report().get_balances()
        self.assertAlmostEqual(balances["total_owed"], 34.0)
        [row] = balances["customers"]
        self.assertEqual(row["id"], self.customer.id)
        self.assertAlmostEqual(row["balance"], 34.0)
        self.assertTrue(row["last_payment"])
        self.assertEqual(len(balances["flagged"]), 1)
        self.assertIn("encargado", balances["flagged"][0]["issues"][0])

        statement = self._report().get_statement(self.customer.id)
        kinds = [move["kind"] for move in statement["moves"]]
        self.assertEqual(kinds[:2], ["Compra a crédito", "Compra a crédito"])
        self.assertTrue(kinds[2].startswith("Abono"))
        self.assertEqual([move["balance"] for move in statement["moves"]], [36.0, 54.0, 34.0])
        self.assertAlmostEqual(statement["moves"][2]["payment"], 20.0)
        self.assertAlmostEqual(statement["balance"], 34.0)
        first = statement["moves"][0]
        self.assertEqual(first["items"], [{"name": "Refresco 600 ml", "qty": 2.0, "price_unit": 18.0, "total": 36.0}])
        self.assertAlmostEqual(first["ticket_total"], 36.0)
        self.assertEqual(statement["moves"][2]["items"], [], "an abono has no products")

    def test_the_notebook_balance_adds_to_the_debt_without_being_a_sale(self):
        Opening = self.env["vlux.credit.opening"].with_user(self.owner)
        opening = Opening.create({
            "partner_id": self.customer.id, "amount": 250.0, "date": "2026-09-01", "note": "Libreta hoja 3",
            "credit_limit": 1000.0,
        })
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.vlux_credit_balance, 0.0, "a draft is not owed yet")
        opening.action_post()
        self.assertEqual(opening.state, "posted")
        self.assertEqual(opening.sudo().move_id.state, "posted")
        receivable = opening.sudo().move_id.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable")
        self.assertEqual((receivable.partner_id, receivable.debit), (self.customer, 250.0))
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.vlux_credit_balance, 250.0)
        self.assertTrue(self.customer.vlux_credit_allowed)
        self.assertEqual(self.customer.vlux_credit_limit, 1000.0)

        session = self.open_new_session(opening_cash=500.0)
        self._sell_on_credit(2, self.encargado)  # 36
        self.env["pos.session"].vlux_pos_register_abono(
            session.id, self.customer.id, 100.0, self.cash_pm1.id, "opening-abono", self.encargado.id
        )
        self.assertEqual(session.order_ids.filtered(lambda o: not o.vlux_credit_abono).mapped("amount_total"), [36.0],
                         "the notebook is not one of today's sales")
        self.customer.invalidate_recordset()
        self.assertAlmostEqual(self.customer.vlux_credit_balance, 186.0)

        [row] = self._report().get_balances()["customers"]
        self.assertAlmostEqual(row["balance"], 186.0)
        statement = self._report().get_statement(self.customer.id)
        self.assertEqual(statement["moves"][0]["kind"], "Saldo inicial (libreta)")
        self.assertEqual(statement["moves"][0]["reference"], "Libreta hoja 3")
        self.assertEqual([move["balance"] for move in statement["moves"]], [250.0, 286.0, 186.0])

        with self.assertRaises(UserError):
            opening.amount = 300.0
        with self.assertRaises(UserError):
            opening.unlink()
        opening.action_cancel()
        self.assertEqual(opening.state, "cancel")
        self.assertTrue(opening.sudo().move_id.reversal_move_ids, "cancelling reverses the entry")
        self.customer.invalidate_recordset()
        self.assertAlmostEqual(self.customer.vlux_credit_balance, -64.0)

    def test_only_the_owner_loads_notebook_balances(self):
        cashier = new_test_user(
            self.env, login="vlux-opening-cashier", groups="base.group_user,point_of_sale.group_pos_user",
        )
        with self.assertRaises(AccessError):
            self.env["vlux.credit.opening"].with_user(cashier).create({"partner_id": self.customer.id, "amount": 10.0})

    def test_only_the_owner_sees_credit(self):
        cashier = new_test_user(
            self.env, login="vlux-credit-cashier", groups="base.group_user,point_of_sale.group_pos_user",
        )
        with self.assertRaises(AccessError):
            self._report(cashier).get_balances()
        with self.assertRaises(AccessError):
            self._report(cashier).get_statement(self.customer.id)


@tagged("post_install", "-at_install")
class TestVluxCreditOwnerRoutes(HttpCase):
    """The owner app calls these routes; only the owner gets data."""

    def _call(self, route, params=None):
        response = self.url_open(
            route,
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": params or {}, "id": 1}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_owner_gets_balances_and_a_cashier_does_not(self):
        new_test_user(self.env, login="vlux-route-owner", password="vlux-route-owner-pw",
                      groups="base.group_user,point_of_sale.group_pos_user,vlux_core.group_vlux_owner")
        new_test_user(self.env, login="vlux-route-cashier", password="vlux-route-cashier-pw",
                      groups="base.group_user,point_of_sale.group_pos_user")
        partner = self.env["res.partner"].create({"name": "Cliente Ruta"})

        self.authenticate("vlux-route-owner", "vlux-route-owner-pw")
        balances = self._call("/vlux_owner/api/credit")["result"]
        self.assertIn("total_owed", balances)
        statement = self._call("/vlux_owner/api/credit/statement", {"partner_id": partner.id})["result"]
        self.assertEqual(statement["customer"]["name"], "Cliente Ruta")

        self.authenticate("vlux-route-cashier", "vlux-route-cashier-pw")
        self.assertIn("error", self._call("/vlux_owner/api/credit"))
