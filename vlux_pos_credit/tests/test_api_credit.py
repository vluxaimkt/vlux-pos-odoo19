"""Credit through the VLUX API, on the same rules as the Odoo POS (D7).

Only the encargado or the owner authorises credit; a sale on credit that
breaks a rule is kept and flagged, never refused; abonos lower the balance,
are idempotent and enter the closing like any cash.
"""
import json
import uuid

from odoo.tests import tagged

from odoo.addons.pos_hr.tests.test_frontend import TestPosHrHttpCommon
from odoo.addons.vlux_core.tests.test_api_v1 import API, VluxApiCase

from ..models.pos_order import ISSUE_NOT_MANAGER, ISSUE_OVER_LIMIT


@tagged("post_install", "-at_install")
class TestVluxApiCredit(TestPosHrHttpCommon, VluxApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.main_pos_config
        cls.credit = cls.env["pos.payment.method"]._vlux_credit_method(cls.config.company_id)
        cls.config.write({"payment_method_ids": [(4, cls.credit.id)], "use_pricelist": False})
        cls.cash = cls.config.payment_method_ids.filtered("is_cash_count")[:1]
        cls.soda = cls.env["product.template"].create({
            "name": "Refresco", "list_price": 20.0, "available_in_pos": True, "taxes_id": False,
        }).product_variant_id
        cls.lupe = cls.env["res.partner"].create({"name": "Lupe Fiado", "phone": "722 111 2222"})
        cls.cashier = cls._make_user("api-credit-cashier", "vlux_core.group_vlux_cashier")
        cls.raw = cls.env["vlux.api.token"].issue(
            "Caja PWA", "system:read catalog:read orders:write session:manage", user=cls.cashier,
            pos_config=cls.config,
        )[1]

    def _call(self, method, path, body=None):
        headers = {"Authorization": "Bearer " + self.raw}
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self.url_open(API + path, data=json.dumps(body) if body is not None else None,
                                 headers=headers, method=method)
        return response, json.loads(response.text)

    def _open(self):
        return self._call("POST", "/registers/%d/session/open" % self.config.id,
                          {"opening_cash": 100.0, "employee_id": self.manager1.id})

    def _authorize(self, limit=100.0, employee=None):
        return self._call("POST", "/credit/customers/%d" % self.lupe.id, {
            "register_id": self.config.id, "employee_id": (employee or self.manager1).id,
            "allowed": True, "limit": limit,
        })

    def _sell_on_credit(self, qty, employee, partner=None, credit=None):
        amount = 20.0 * qty
        return self._call("POST", "/orders", {
            "uuid": str(uuid.uuid4()), "register_id": self.config.id, "employee_id": employee.id,
            "partner_id": (partner or self.lupe).id if partner is not False else None,
            "lines": [{"product_id": self.soda.id, "qty": qty}],
            "payments": [{"payment_method_id": self.credit.id, "amount": amount if credit is None else credit}],
            "expected_total": amount,
        })

    # --- authorising ------------------------------------------------------------------

    def test_only_a_manager_authorises_credit_and_sets_the_limit(self):
        response, body = self._authorize(employee=self.emp2)
        self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN"), body)
        self.assertFalse(self.lupe.vlux_credit_allowed)

        response, body = self._authorize(limit=150.0)
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["data"], {
            "partner_id": self.lupe.id, "name": "Lupe Fiado", "phone": "722 111 2222", "allowed": True,
            "limit": 150.0, "balance": 0.0, "available": 150.0, "over_limit": False,
        })
        _response, listed = self._call("GET", "/credit/customers")
        self.assertIn(self.lupe.id, [row["partner_id"] for row in listed["data"]["items"]])

    # --- selling on credit --------------------------------------------------------------

    def test_a_sale_on_credit_by_the_manager_raises_the_balance(self):
        self._open()
        self._authorize(limit=100.0)
        response, body = self._sell_on_credit(2, self.manager1)
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(body["data"]["credit"], {
            "amount": 40.0, "abono": False, "previous_balance": 0.0, "new_balance": 40.0,
            "flagged": False, "issues": [],
        })
        _response, row = self._call("GET", "/credit/customers/%d" % self.lupe.id)
        self.assertEqual((row["data"]["balance"], row["data"]["available"]), (40.0, 60.0))

    def test_rule_breaking_sales_are_kept_and_flagged(self):
        self._open()
        self._authorize(limit=50.0)
        response, body = self._sell_on_credit(1, self.emp2)
        self.assertEqual(response.status_code, 200, body)
        self.assertTrue(body["data"]["credit"]["flagged"])
        self.assertIn(ISSUE_NOT_MANAGER, body["data"]["credit"]["issues"])

        response, body = self._sell_on_credit(3, self.manager1)
        self.assertEqual(response.status_code, 200, "an over-limit sale is kept")
        self.assertIn(ISSUE_OVER_LIMIT, body["data"]["credit"]["issues"])
        self.assertEqual(body["data"]["credit"]["previous_balance"], 20.0)

    def test_credit_needs_a_customer(self):
        self._open()
        response, body = self._sell_on_credit(1, self.manager1, partner=False)
        self.assertEqual((response.status_code, body["error"]), (400, "VALIDATION_ERROR"))
        self.assertIn("cliente", body["message"])

    # --- abonos ---------------------------------------------------------------------------

    def test_an_abono_lowers_the_balance_once_and_enters_the_closing(self):
        _response, opened = self._open()
        self._authorize(limit=100.0)
        self._sell_on_credit(2, self.manager1)
        abono = {"uuid": str(uuid.uuid4()), "register_id": self.config.id, "partner_id": self.lupe.id,
                 "amount": 15.0, "payment_method_id": self.cash.id, "employee_id": self.emp2.id}

        response, body = self._call("POST", "/credit/abonos", abono)
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual((body["data"]["previous_balance"], body["data"]["new_balance"]), (40.0, 25.0))
        self.assertEqual(body["data"]["customer"]["balance"], 25.0)

        response, again = self._call("POST", "/credit/abonos", abono)
        self.assertEqual(again["data"]["order_id"], body["data"]["order_id"], "a retried abono is the same abono")
        _response, row = self._call("GET", "/credit/customers/%d" % self.lupe.id)
        self.assertEqual(row["data"]["balance"], 25.0)

        for bad in ({"amount": 999.0}, {"payment_method_id": self.credit.id}, {"payment_method_id": None}):
            response, body = self._call("POST", "/credit/abonos", {**abono, "uuid": str(uuid.uuid4()), **bad})
            self.assertEqual((response.status_code, body["error"]), (400, "VALIDATION_ERROR"), bad)

        _response, closing = self._call("GET", "/registers/%d/session/closing" % self.config.id)
        self.assertEqual(closing["data"]["cash"]["sales"], 15.0, "the abono's cash is in the drawer")
        self.assertEqual(closing["data"]["session"]["id"], opened["data"]["session"]["id"])

    def test_no_abono_without_an_open_register(self):
        response, body = self._call("POST", "/credit/abonos", {
            "uuid": str(uuid.uuid4()), "register_id": self.config.id, "partner_id": self.lupe.id,
            "amount": 5.0, "payment_method_id": self.cash.id, "employee_id": self.emp2.id,
        })
        self.assertEqual((response.status_code, body["error"]), (409, "NO_OPEN_SESSION"))

    # --- customers ----------------------------------------------------------------------

    def test_new_customers_follow_odoo_contact_creation_rights(self):
        response, body = self._call("POST", "/customers", {"name": "Don Chuy", "phone": "722 333 4444"})
        self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN"), "no contact creation right")

        self.cashier.sudo().group_ids = [(4, self.env.ref("base.group_partner_manager").id)]
        response, body = self._call("POST", "/customers", {"name": "Don Chuy", "phone": "722 333 4444"})
        self.assertEqual(response.status_code, 200, body)
        self.assertEqual(self.env["res.partner"].browse(body["data"]["id"]).name, "Don Chuy")

        response, body = self._call("POST", "/customers", {"name": "  "})
        self.assertEqual((response.status_code, body["error"]), (400, "VALIDATION_ERROR"))
