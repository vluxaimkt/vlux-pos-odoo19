import json

from odoo.exceptions import AccessError
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
