"""Phase C over HTTP: open the register, sell, sell the same sale twice, close.

The acceptance criteria of the phase (docs/PLAN_INICIATIVAS.md): a sale sent
twice produces one order; the total charged matches the total booked to the
cent; the closing balances and keeps the difference limit.
"""
import hashlib
import json
import uuid

from odoo.tests import tagged

from odoo.addons.pos_hr.tests.test_frontend import TestPosHrHttpCommon
from odoo.addons.vlux_core.tests.test_api_v1 import API, VluxApiCase


@tagged("post_install", "-at_install")
class TestVluxApiSales(TestPosHrHttpCommon, VluxApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.main_pos_config
        cls.config.write({
            "cash_control": True,
            "set_maximum_difference": True,
            "amount_authorized_diff": 30.0,
            "use_pricelist": False,
        })
        cls.cash = cls.config.payment_method_ids.filtered("is_cash_count")[:1]
        cls.card = cls.config.payment_method_ids.filtered(lambda m: m.type == "bank")[:1]
        iva = cls.env["account.tax"].create({
            "name": "IVA 16% incluido",
            "amount": 16.0,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "price_include_override": "tax_included",
            "company_id": cls.config.company_id.id,
        })
        Template = cls.env["product.template"]
        cls.soda = Template.create({
            "name": "Refresco 600 ml", "list_price": 20.0, "available_in_pos": True,
            "taxes_id": [(6, 0, iva.ids)],
        }).product_variant_id
        cls.cookies = Template.create({
            "name": "Galletas", "list_price": 15.5, "available_in_pos": True, "taxes_id": False,
        }).product_variant_id
        cls.hidden = Template.create({
            "name": "Fuera del POS", "list_price": 5.0, "available_in_pos": False,
        }).product_variant_id

        cls.cashier = cls._make_user("api-sales-cashier", "vlux_core.group_vlux_cashier")
        Token = cls.env["vlux.api.token"]
        _token, cls.raw = Token.issue(
            "Caja PWA", "system:read catalog:read orders:write session:manage", user=cls.cashier,
        )
        _token, cls.raw_read_only = Token.issue("Solo estado", "system:read", user=cls.cashier)

    # --- helpers ----------------------------------------------------------------

    def _call(self, method, path, body=None, token=None):
        headers = {"Authorization": "Bearer " + (token or self.raw)}
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self.url_open(
            API + path, data=json.dumps(body) if body is not None else None,
            headers=headers, method=method,
        )
        return response, json.loads(response.text)

    def _open(self, opening_cash=100.0, employee=None):
        return self._call("POST", "/registers/%d/session/open" % self.config.id, {
            "opening_cash": opening_cash, "employee_id": (employee or self.emp2).id,
        })

    def _sale(self, payments, lines=None, **extra):
        body = {
            "uuid": str(uuid.uuid4()),
            "register_id": self.config.id,
            "employee_id": self.emp2.id,
            "lines": lines or [
                {"product_id": self.soda.id, "qty": 2},
                {"product_id": self.cookies.id, "qty": 1},
            ],
            "payments": [{"payment_method_id": method.id, "amount": amount} for method, amount in payments],
            **extra,
        }
        return body

    def _orders(self):
        return self.env["pos.order"].search([("config_id", "=", self.config.id)])

    # --- register -----------------------------------------------------------------

    def test_register_state_and_employees(self):
        response, body = self._call("GET", "/registers/%d/session" % self.config.id)
        self.assertEqual(response.status_code, 200, body)
        self.assertIsNone(body["data"]["session"])
        self.assertTrue(body["data"]["employee_login"])
        self.assertEqual(body["data"]["max_difference"], 30.0)

        _response, body = self._call("GET", "/registers/%d/employees" % self.config.id)
        employees = {row["id"]: row for row in body["data"]["items"]}
        self.assertEqual(employees[self.emp2.id]["pin_sha1"], hashlib.sha1(b"1234").hexdigest())
        self.assertEqual(employees[self.manager1.id]["role"], "manager")
        self.assertEqual(employees[self.emp2.id]["role"], "cashier")

        response, body = self._call("GET", "/registers/%d/employees" % self.config.id, token=self.raw_read_only)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(body["error"], "FORBIDDEN_SCOPE", "PIN hashes only go to a token that may sell")

        response, body = self._call("GET", "/registers/999999/session")
        self.assertEqual(body["error"], "NOT_FOUND")

    def test_opening_needs_an_allowed_employee_and_is_safe_to_repeat(self):
        response, body = self._call("POST", "/registers/%d/session/open" % self.config.id, {"opening_cash": 50})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["error"], "VALIDATION_ERROR")
        self.assertFalse(self.config.current_session_id, "a refused opening leaves no session behind")

        response, body = self._open(opening_cash=100.0)
        self.assertEqual(response.status_code, 200, body)
        session = body["data"]["session"]
        self.assertEqual(session["state"], "opened")
        self.assertEqual(session["opening_cash"], 100.0)
        self.assertEqual(session["employee_id"], self.emp2.id)
        self.assertFalse(body["data"]["already_open"])

        _response, again = self._open(opening_cash=999.0)
        self.assertTrue(again["data"]["already_open"])
        self.assertEqual(again["data"]["session"]["id"], session["id"])
        self.assertEqual(again["data"]["session"]["opening_cash"], 100.0)

    # --- selling ------------------------------------------------------------------

    def test_quote_prices_on_the_server(self):
        response, body = self._call("POST", "/orders/quote", {
            "register_id": self.config.id,
            "lines": [{"product_id": self.soda.id, "qty": 2}, {"product_id": self.cookies.id, "qty": 1}],
        })
        self.assertEqual(response.status_code, 200, body)
        quote = body["data"]
        self.assertEqual(quote["amount_total"], 55.5)
        self.assertEqual(quote["amount_tax"], 5.52)  # 40 - 40 / 1.16
        self.assertEqual(quote["amount_untaxed"], 49.98)
        self.assertFalse(any(line["price_overridden"] for line in quote["lines"]))

        _response, body = self._call("POST", "/orders/quote", {
            "register_id": self.config.id, "lines": [{"product_id": self.hidden.id, "qty": 1}],
        })
        self.assertEqual(body["error"], "VALIDATION_ERROR", "a product not sold at the register cannot be priced")

    def test_a_sale_sent_twice_is_recorded_once_and_matches_the_books(self):
        self._open()
        sale = self._sale([(self.cash, 100.0)], expected_total=55.5)

        response, body = self._call("POST", "/orders", sale)
        self.assertEqual(response.status_code, 200, body)
        recorded = body["data"]
        self.assertFalse(recorded["duplicate"])
        self.assertEqual(recorded["state"], "paid")
        self.assertEqual(recorded["amount_total"], 55.5)
        self.assertEqual(recorded["amount_paid"], 55.5)
        self.assertEqual(recorded["change"], 44.5)

        response, again = self._call("POST", "/orders", sale)
        self.assertEqual(response.status_code, 200, again)
        self.assertTrue(again["data"]["duplicate"])
        self.assertEqual(again["data"]["id"], recorded["id"])
        self.assertEqual(len(self._orders()), 1, "a retried sale must not create a second order")

        order = self._orders()
        self.assertEqual(order.source, "vlux_api")
        self.assertEqual(order.employee_id, self.emp2)
        # Odoo's own computation over the stored lines agrees to the cent.
        order._compute_prices()
        self.assertAlmostEqual(order.amount_total, 55.5, places=2)
        self.assertAlmostEqual(order.amount_tax, 5.52, places=2)

        _response, found = self._call("GET", "/orders/%s" % sale["uuid"])
        self.assertEqual(found["data"]["id"], recorded["id"])

    def test_refused_sales_leave_nothing_written(self):
        self._open()
        cases = [
            (self._sale([(self.cash, 50.0)]), "PAYMENT_MISMATCH"),               # short
            (self._sale([(self.card, 60.0)]), "PAYMENT_MISMATCH"),               # card cannot give change
            (self._sale([(self.cash, 60.0)], expected_total=54.0), "TOTAL_MISMATCH"),
        ]
        for sale, code in cases:
            response, body = self._call("POST", "/orders", sale)
            self.assertEqual(response.status_code, 409, body)
            self.assertEqual(body["error"], code)
        self.assertFalse(self._orders())

        response, body = self._call("POST", "/orders", self._sale([(self.card, 30.0), (self.cash, 30.0)]))
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["data"]["change"], 4.5, "cash pays the change of a mixed payment")

    def test_a_price_different_from_the_catalog_is_kept_and_flagged(self):
        self._open()
        sale = self._sale([(self.cash, 18.0)], lines=[{"product_id": self.soda.id, "qty": 1, "price_unit": 18.0}])
        response, body = self._call("POST", "/orders", sale)
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["data"]["amount_total"], 18.0)
        self.assertTrue(body["data"]["price_overridden"])
        self.assertTrue(self._orders().vlux_api_price_overridden)

    def test_selling_on_a_closed_register_is_refused(self):
        response, body = self._call("POST", "/orders", self._sale([(self.cash, 100.0)]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["error"], "NO_OPEN_SESSION")

    # --- closing ------------------------------------------------------------------

    def test_closing_keeps_the_difference_limit(self):
        _response, opened = self._open(opening_cash=100.0)
        session_id = opened["data"]["session"]["id"]
        self._call("POST", "/orders", self._sale([(self.cash, 60.0)]))
        self._call("POST", "/orders", self._sale([(self.card, 55.5)]))

        response, summary = self._call("GET", "/registers/%d/session/closing" % self.config.id)
        self.assertEqual(response.status_code, 200, summary)
        self.assertEqual(summary["data"]["cash"]["expected"], 155.5)
        card = next(m for m in summary["data"]["other_methods"] if m["payment_method_id"] == self.card.id)
        self.assertEqual(card["expected"], 55.5)

        close = {
            "session_id": session_id, "employee_id": self.emp2.id,
            "counted_cash": 105.5, "counted": [{"payment_method_id": self.card.id, "amount": 55.5}],
        }
        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id, close)
        self.assertEqual(response.status_code, 409, body)
        self.assertEqual(body["error"], "CLOSING_REFUSED", "a cashier cannot close $50 short")
        session = self.env["pos.session"].browse(session_id)
        self.assertEqual(session.state, "opened", "a refused closing leaves the register open")

        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id,
                                    {**close, "session_id": session_id + 1000})
        self.assertEqual(body["error"], "CONFLICT", "a stale screen cannot close another session")

        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id,
                                    {**close, "counted_cash": 155.5})
        self.assertEqual(response.status_code, 200, body)
        self.assertIsNone(body["data"]["session"])
        session.invalidate_recordset()
        self.assertEqual(session.state, "closed")
        self.assertEqual(session.cash_register_balance_end_real, 155.5)

    def test_a_manager_may_close_above_the_limit(self):
        _response, opened = self._open(opening_cash=100.0)
        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id, {
            "session_id": opened["data"]["session"]["id"], "employee_id": self.manager1.id, "counted_cash": 40.0,
        })
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(self.env["pos.session"].browse(opened["data"]["session"]["id"]).state, "closed")
