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

    # PINs of the pos_hr fixture employees.
    PINS = {"emp1": "2580", "emp2": "1234", "manager1": "5651"}

    def _login(self, employee, pin=None, token=None):
        """POST /employees/login; returns (response, body)."""
        if pin is None:
            pin = employee.sudo().pin
        return self._call("POST", "/registers/%d/employees/login" % self.config.id,
                          {"employee_id": employee.id, "pin": pin}, token=token)

    def _session(self, employee, token=None):
        response, body = self._login(employee, token=token)
        self.assertEqual(response.status_code, 200, body)
        return body["data"]["session"]

    def _call(self, method, path, body=None, token=None, employee=None):
        headers = {"Authorization": "Bearer " + (token or self.raw)}
        if employee is not None:
            headers["X-Vlux-Employee"] = self._session(employee, token=token)
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self.url_open(
            API + path, data=json.dumps(body) if body is not None else None,
            headers=headers, method=method,
        )
        return response, json.loads(response.text)

    def _open(self, opening_cash=100.0, employee=None):
        employee = employee or self.emp2
        return self._call("POST", "/registers/%d/session/open" % self.config.id, {
            "opening_cash": opening_cash, "employee_id": employee.id,
        }, employee=employee)

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
        self.assertEqual((response.status_code, body["error"]), (401, "PIN_REQUIRED"), "nobody proved who opens")
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
        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id, close, employee=self.emp2)
        self.assertEqual(response.status_code, 409, body)
        self.assertEqual(body["error"], "CLOSING_REFUSED", "a cashier cannot close $50 short")
        session = self.env["pos.session"].browse(session_id)
        self.assertEqual(session.state, "opened", "a refused closing leaves the register open")

        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id,
                                    {**close, "session_id": session_id + 1000}, employee=self.emp2)
        self.assertEqual(body["error"], "CONFLICT", "a stale screen cannot close another session")

        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id,
                                    {**close, "counted_cash": 155.5}, employee=self.emp2)
        self.assertEqual(response.status_code, 200, body)
        self.assertIsNone(body["data"]["session"])
        session.invalidate_recordset()
        self.assertEqual(session.state, "closed")
        self.assertEqual(session.cash_register_balance_end_real, 155.5)

    def test_cash_in_and_out_change_what_the_closing_expects(self):
        _response, opened = self._open(opening_cash=100.0)
        session_id = opened["data"]["session"]["id"]
        path = "/registers/%d/session/cash-move" % self.config.id
        move = {"session_id": session_id, "uuid": str(uuid.uuid4()), "type": "out", "amount": 35.0,
                "reason": "Pago al proveedor de refrescos"}

        response, body = self._call("POST", path, move)
        self.assertEqual((response.status_code, body["error"]), (401, "PIN_REQUIRED"))
        response, body = self._call("POST", path, {**move, "type": "in"}, employee=self.emp2)
        self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN"), "a cashier does not put cash in")
        self.emp4.sudo().pin = "4321"
        response, body = self._call("POST", path, {**move, "uuid": str(uuid.uuid4())}, employee=self.emp4)
        self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN"), "minimal access moves no cash")

        response, body = self._call("POST", path, move, employee=self.manager1)
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["data"]["type"], "out")
        self.assertEqual(body["data"]["amount"], 35.0)
        self.assertEqual(body["data"]["employee"], self.manager1.name)
        self.assertFalse(body["data"]["duplicate"])
        _response, again = self._call("POST", path, move, employee=self.manager1)
        self.assertTrue(again["data"]["duplicate"], "the same move sent twice is recorded once")

        response, body = self._call("POST", path, {**move, "uuid": str(uuid.uuid4()), "type": "in", "amount": 50,
                                                   "reason": "Cambio del banco"}, employee=self.manager1)
        self.assertEqual(response.status_code, 200, body)
        for bad in ({"amount": 0}, {"amount": -5}, {"reason": "  "}, {"type": "sideways"}):
            response, body = self._call("POST", path, {**move, "uuid": str(uuid.uuid4()), **bad}, employee=self.manager1)
            self.assertEqual(body["error"], "VALIDATION_ERROR", bad)

        # A cashier pays a supplier from the drawer only where the register allows it.
        cashier_out = {**move, "uuid": str(uuid.uuid4()), "amount": 15, "reason": "Pago al repartidor de pan"}
        response, body = self._call("POST", path, cashier_out, employee=self.emp2)
        self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN"), "off by default, as in Odoo")
        self.config.vlux_cashier_cash_out = True
        _response, state = self._call("GET", "/registers/%d/session" % self.config.id)
        self.assertTrue(state["data"]["cashier_cash_out"], "the screen learns the register's option")
        response, body = self._call("POST", path, cashier_out, employee=self.emp2)
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["data"]["employee"], self.emp2.name)

        _response, listed = self._call("GET", "/registers/%d/session/cash-moves" % self.config.id)
        self.assertEqual([(m["type"], m["amount"]) for m in listed["data"]["items"]],
                         [("out", 35.0), ("in", 50.0), ("out", 15.0)])
        _response, summary = self._call("GET", "/registers/%d/session/closing" % self.config.id)
        self.assertEqual(summary["data"]["cash"]["expected"], 100.0, "100 opening - 35 out + 50 in - 15 out")
        self.assertEqual(len(summary["data"]["cash"]["moves"]), 3)

    def test_a_manager_adds_an_unknown_product_from_the_register(self):
        if not hasattr(self.env["product.template"], "_vlux_quick_create_for"):
            self.skipTest("vlux_pos_catalog is not installed")
        path = "/registers/%d/products" % self.config.id
        new = {"name": "Chicles menta", "barcode": "7501234567895", "list_price": 12.0}
        response, body = self._call("POST", path, new, employee=self.manager1)
        self.assertEqual(body["error"], "NO_OPEN_SESSION")
        self._open()

        response, body = self._call("POST", path, new)
        self.assertEqual((response.status_code, body["error"]), (401, "PIN_REQUIRED"))
        response, body = self._call("POST", path, new, employee=self.emp2)
        self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN"), "a cashier does not set prices")

        response, body = self._call("POST", path, new, employee=self.manager1)
        self.assertEqual(response.status_code, 200, body)
        product = self.env["product.product"].browse(body["data"]["id"])
        self.assertEqual((product.name, product.barcode, product.list_price), ("Chicles menta", "7501234567895", 12.0))
        self.assertTrue(product.available_in_pos)
        self.assertEqual(body["data"]["barcode"], "7501234567895", "the register gets it as the catalog feed sends it")

        response, body = self._call("POST", path, {**new, "name": "Otro"}, employee=self.manager1)
        self.assertEqual((response.status_code, body["error"]), (409, "CONFLICT"), "a taken barcode is not reused")
        response, body = self._call("POST", path, {**new, "barcode": "7501234567888", "name": ""}, employee=self.manager1)
        self.assertEqual(body["error"], "VALIDATION_ERROR")

    def test_a_manager_may_close_above_the_limit(self):
        _response, opened = self._open(opening_cash=100.0)
        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id, {
            "session_id": opened["data"]["session"]["id"], "employee_id": self.manager1.id, "counted_cash": 40.0,
        }, employee=self.manager1)
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(self.env["pos.session"].browse(opened["data"]["session"]["id"]).state, "closed")

    # --- a register's token ---------------------------------------------------------

    def test_a_token_bound_to_a_register_cannot_touch_another(self):
        other = self.env["pos.config"].create({"name": "Otra caja"})
        _token, bound = self.env["vlux.api.token"].issue(
            "Caja atada", "system:read catalog:read orders:write session:manage",
            user=self.cashier, pos_config=self.config, lifetime_days=30,
        )

        response, body = self._call("GET", "/registers/%d/session" % self.config.id, token=bound)
        self.assertEqual(response.status_code, 200, body)

        for method, path, payload in (
            ("GET", "/registers/%d/session" % other.id, None),
            ("GET", "/registers/%d/employees" % other.id, None),
            ("POST", "/registers/%d/session/open" % other.id, {"opening_cash": 0}),
            ("POST", "/orders/quote", {"register_id": other.id, "lines": [{"product_id": self.soda.id, "qty": 1}]}),
            ("POST", "/orders", self._sale([(self.cash, 20.0)], register_id=other.id)),
        ):
            response, body = self._call(method, path, payload, token=bound)
            self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN"), path)
        self.assertFalse(other.current_session_id, "nothing was opened on the other register")

        _response, body = self._call("GET", "/store/config", token=bound)
        self.assertEqual([r["id"] for r in body["data"]["registers"]], [self.config.id])

        _response, body = self._call("GET", "/me", token=bound)
        self.assertEqual(body["data"]["token"]["register_id"], self.config.id)

    def test_a_rotated_register_token_stays_bound(self):
        token, _raw = self.env["vlux.api.token"].issue(
            "Caja atada", "orders:write", user=self.cashier, pos_config=self.config, lifetime_days=30,
        )
        new, _new_raw = token.rotate()
        self.assertEqual(new.pos_config_id, self.config)
        self.assertEqual(new.company_id, self.config.company_id)

    def test_a_manager_without_a_pin_cannot_close(self):
        # A real manager (POS manager user) whose PIN was never set.
        boss = self.manager1
        boss.pin = False
        _response, opened = self._open(opening_cash=100.0)
        response, body = self._login(boss, pin="")
        self.assertEqual((response.status_code, body["error"]), (400, "VALIDATION_ERROR"), "a manager needs a PIN")
        self.assertIn("PIN", body["message"])
        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id, {
            "session_id": opened["data"]["session"]["id"], "employee_id": boss.id, "counted_cash": 40.0,
        })
        self.assertEqual((response.status_code, body["error"]), (401, "PIN_REQUIRED"), body)
        self.assertEqual(self.env["pos.session"].browse(opened["data"]["session"]["id"]).state, "opened")

        boss.pin = "7391"
        response, body = self._call("POST", "/registers/%d/session/close" % self.config.id, {
            "session_id": opened["data"]["session"]["id"], "employee_id": boss.id, "counted_cash": 40.0,
        }, employee=boss)
        self.assertEqual(response.status_code, 200, body)

    # --- the PIN checked by the server --------------------------------------------------

    def test_the_server_checks_the_pin_and_locks_after_five_wrong_ones(self):
        response, body = self._login(self.emp2, pin="0000")
        self.assertEqual((response.status_code, body["error"]), (401, "INVALID_PIN"))
        response, body = self._login(self.emp2)
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["data"]["employee"], {"id": self.emp2.id, "name": self.emp2.name, "role": "cashier"})
        self.assertTrue(body["data"]["session"])
        self.assertNotIn(self.emp2.pin, response.text, "the PIN never comes back")

        # In production each wrong PIN is counted in a transaction of its own
        # and survives the refusal; a test shares one connection and the
        # refusal's savepoint would undo it, so the failures are counted here.
        limiter = self.env["vlux.rate.limit"].sudo()
        identity = "%s:%s" % (self.config.id, self.emp1.id)
        for _attempt in range(5):
            limiter.consume("pin", identity, 5, 900)
        self.assertTrue(limiter.exhausted("pin", identity, 5, 900))
        response, body = self._login(self.emp1)
        self.assertEqual((response.status_code, body["error"]), (429, "PIN_LOCKED"), "even the right PIN waits")
        response, body = self._login(self.emp2)
        self.assertEqual(response.status_code, 200, "the lock is per employee")
        limiter.reset("pin", identity)
        self.assertFalse(limiter.exhausted("pin", identity, 5, 900), "a right PIN clears the count")

    def test_a_session_only_works_on_its_register_device_and_until_logout(self):
        session = self._session(self.emp2)
        other_token = self.env["vlux.api.token"].issue(
            "Otra tablet", "system:read catalog:read orders:write session:manage", user=self.cashier,
        )[1]
        headers = {"Authorization": "Bearer " + other_token, "X-Vlux-Employee": session, "Content-Type": "application/json"}
        response = self.url_open(API + "/registers/%d/session/open" % self.config.id,
                                 data=json.dumps({"opening_cash": 10}), headers=headers, method="POST")
        self.assertEqual(json.loads(response.text)["error"], "PIN_REQUIRED", "a session is tied to its device's token")

        headers = {"Authorization": "Bearer " + self.raw, "X-Vlux-Employee": session, "Content-Type": "application/json"}
        response = self.url_open(API + "/registers/%d/session/open" % self.config.id,
                                 data=json.dumps({"opening_cash": 10, "employee_id": self.manager1.id}), headers=headers, method="POST")
        self.assertEqual(json.loads(response.text)["error"], "VALIDATION_ERROR", "cannot claim someone else")

        self.url_open(API + "/registers/%d/employees/logout" % self.config.id, data="{}", headers=headers, method="POST")
        response = self.url_open(API + "/registers/%d/session/open" % self.config.id,
                                 data=json.dumps({"opening_cash": 10}), headers=headers, method="POST")
        self.assertEqual(json.loads(response.text)["error"], "PIN_REQUIRED", "a logged-out session is dead")

    def test_sales_record_whether_the_employee_was_verified(self):
        self._open()
        verified = self._sale([(self.cash, 100.0)], employee_session=self._session(self.emp2))
        response, body = self._call("POST", "/orders", verified)
        self.assertEqual(response.status_code, 200, body)
        offline = self._sale([(self.cash, 100.0)])
        response, body = self._call("POST", "/orders", offline)
        self.assertEqual(response.status_code, 200, "an offline-checked sale is never refused")
        orders = self.env["pos.order"].search([("uuid", "in", [verified["uuid"], offline["uuid"]])])
        flags = {order.uuid: order.vlux_api_employee_verified for order in orders}
        self.assertEqual(flags, {verified["uuid"]: True, offline["uuid"]: False})

    # --- returns ------------------------------------------------------------------------

    def _sold(self):
        self._open()
        sale = self._sale([(self.cash, 100.0)])
        _response, body = self._call("POST", "/orders", sale)
        return body["data"]

    def test_a_return_of_part_of_a_sale(self):
        sold = self._sold()
        soda_line = next(line for line in sold["lines"] if line["product_id"] == self.soda.id)
        self.assertEqual(soda_line["refundable_qty"], 2.0)

        _response, found = self._call("GET", "/orders/lookup?reference=%s" % sold["pos_reference"])
        self.assertEqual([row["id"] for row in found["data"]["items"]], [sold["id"]])
        _response, recent = self._call("GET", "/orders/recent?register_id=%d" % self.config.id)
        self.assertEqual(recent["data"]["items"][0]["id"], sold["id"])

        lines = [{"line_id": soda_line["id"], "qty": 1}]
        _response, quote = self._call("POST", "/orders/refund/quote",
                                      {"register_id": self.config.id, "order_id": sold["id"], "lines": lines})
        self.assertEqual(quote["data"]["amount_refund"], 20.0)

        refund = {"uuid": str(uuid.uuid4()), "register_id": self.config.id, "order_id": sold["id"], "lines": lines,
                  "payments": [{"payment_method_id": self.cash.id, "amount": 20.0}]}
        response, body = self._call("POST", "/orders/refund", refund)
        self.assertEqual((response.status_code, body["error"]), (401, "PIN_REQUIRED"), "cash leaves the drawer")

        response, body = self._call("POST", "/orders/refund", refund, employee=self.emp2)
        self.assertEqual(response.status_code, 200, body)
        self.assertTrue(body["data"]["is_refund"])
        self.assertEqual(body["data"]["amount_total"], -20.0)
        self.assertEqual(body["data"]["refunded_order_id"], sold["id"])
        order = self.env["pos.order"].browse(body["data"]["id"])
        self.assertEqual(order.lines.qty, -1.0)
        self.assertEqual(order.lines.refunded_orderline_id.id, soda_line["id"])
        self.assertEqual(order.state, "paid")

        _response, again = self._call("POST", "/orders/refund", refund, employee=self.emp2)
        self.assertEqual(again["data"]["id"], order.id, "a retried return is the same return")

        _response, closing = self._call("GET", "/registers/%d/session/closing" % self.config.id, employee=self.emp2)
        self.assertEqual(closing["data"]["cash"]["sales"], 55.5 - 20.0, "the refund left the drawer")

    def test_a_return_never_exceeds_what_was_sold_or_mismatches_the_money(self):
        sold = self._sold()
        soda_line = next(line for line in sold["lines"] if line["product_id"] == self.soda.id)
        base = {"register_id": self.config.id, "order_id": sold["id"]}
        response, body = self._call("POST", "/orders/refund", {
            **base, "uuid": str(uuid.uuid4()), "lines": [{"line_id": soda_line["id"], "qty": 3}],
            "payments": [{"payment_method_id": self.cash.id, "amount": 60.0}]}, employee=self.emp2)
        self.assertEqual((response.status_code, body["error"]), (400, "VALIDATION_ERROR"))
        response, body = self._call("POST", "/orders/refund", {
            **base, "uuid": str(uuid.uuid4()), "lines": [{"line_id": soda_line["id"], "qty": 1}],
            "payments": [{"payment_method_id": self.cash.id, "amount": 25.0}]}, employee=self.emp2)
        self.assertEqual((response.status_code, body["error"]), (409, "PAYMENT_MISMATCH"))
        self._call("POST", "/orders/refund", {
            **base, "uuid": str(uuid.uuid4()), "lines": [{"line_id": soda_line["id"], "qty": 2}],
            "payments": [{"payment_method_id": self.cash.id, "amount": 40.0}]}, employee=self.emp2)
        response, body = self._call("POST", "/orders/refund", {
            **base, "uuid": str(uuid.uuid4()), "lines": [{"line_id": soda_line["id"], "qty": 1}],
            "payments": [{"payment_method_id": self.cash.id, "amount": 20.0}]}, employee=self.emp2)
        self.assertEqual((response.status_code, body["error"]), (400, "VALIDATION_ERROR"), "nothing left to return")

    # --- sold by weight ---------------------------------------------------------------

    def test_weights_add_up_like_the_company_rounds(self):
        """The PWA prices weighed products offline with this same rule (sale/pricing.ts)."""
        iva = self.soda.taxes_id
        ham = self.env["product.template"].create({
            "name": "Jamón", "list_price": 17.40, "available_in_pos": True, "to_weight": True,
            "taxes_id": [(6, 0, iva.ids)],
        }).product_variant_id
        lines = [{"product_id": ham.id, "qty": 0.375}, {"product_id": ham.id, "qty": 0.125}]
        company = self.config.company_id
        totals = {}
        for mode in ("round_per_line", "round_globally"):
            company.tax_calculation_rounding_method = mode
            _response, body = self._call("POST", "/orders/quote", {"register_id": self.config.id, "lines": lines})
            totals[mode] = body["data"]["amount_total"]
        # 6.525 + 2.175: rounded per line 6.53 + 2.18; rounded once 8.70.
        self.assertEqual(totals, {"round_per_line": 8.71, "round_globally": 8.70})

        _response, config = self._call("GET", "/store/config")
        self.assertEqual(config["data"]["tax_rounding"], "round_globally")
        self.assertIn("rules", config["data"]["barcode_nomenclature"])
        # The feed holds back the last 30 s of changes; the payload is what it will send.
        from odoo.addons.vlux_core.controllers.api_catalog import _product_payload
        self.assertTrue(_product_payload(ham)["to_weight"])

    def test_a_price_read_from_a_scale_label_is_not_an_override(self):
        self._open()
        sale = self._sale([(self.cash, 50.0)], lines=[{"product_id": self.soda.id, "qty": 1, "price_unit": 37.5,
                                                       "price_from_barcode": True}])
        response, body = self._call("POST", "/orders", sale)
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["data"]["amount_total"], 37.5)
        self.assertFalse(body["data"]["price_overridden"])
