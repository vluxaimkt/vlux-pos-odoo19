import json

from odoo.tests.common import HttpCase, tagged

from .common import VluxCatalogCase

API = "/vlux/api/v1"


@tagged("post_install", "-at_install")
class TestVluxApiQuickCreate(HttpCase, VluxCatalogCase):
    """``POST /catalog/products`` behaves exactly like the POS quick-create dialog."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Token = cls.env["vlux.api.token"]
        cls.owner_raw = Token.issue("Owner API", "catalog:read catalog:write", user=cls.owner)[1]
        cls.inventory_raw = Token.issue("Inventario API", "catalog:write", user=cls.inventory)[1]
        cls.cashier_raw = Token.issue("Cajero API", "catalog:read", user=cls.cashier)[1]
        cls.env.flush_all()

    def _post(self, body, raw):
        response = self.url_open(
            API + "/catalog/products",
            data=json.dumps(body),
            headers={"Authorization": "Bearer " + raw, "Content-Type": "application/json"},
        )
        return response, json.loads(response.text)

    def test_owner_creates_a_product_that_the_feed_then_returns(self):
        body = {"config_id": self.config_a.id, **self.values(barcode="7501000000901")}
        response, payload = self._post(body, self.owner_raw)

        self.assertEqual(response.status_code, 200, payload)
        product = self.env["product.product"].browse(payload["data"]["product_id"])
        self.assertEqual((product.name, product.barcode, product.lst_price), ("Refresco Cola 600 ml", "7501000000901", 18.5))
        self.assertTrue(product.available_in_pos)
        self.assertEqual(product.company_id, self.company_a)

    def test_duplicate_barcode_is_a_conflict_with_the_existing_product(self):
        first = self.quick_create(self.owner, self.values(barcode="7501000000902"))
        response, payload = self._post(
            {"config_id": self.config_a.id, **self.values(barcode="7501000000902", name="Otro")}, self.owner_raw
        )
        self.assertEqual((response.status_code, payload["error"]), (409, "CONFLICT"))
        self.assertEqual(payload["details"]["existing"]["id"], first["product_id"])

    def test_validation_scope_and_permission_errors_use_the_contract(self):
        response, payload = self._post({"config_id": self.config_a.id, **self.values(name="   ")}, self.owner_raw)
        self.assertEqual((response.status_code, payload["error"]), (400, "VALIDATION_ERROR"))

        response, payload = self._post(self.values(), self.owner_raw)
        self.assertEqual((response.status_code, payload["error"]), (400, "VALIDATION_ERROR"))
        self.assertIn("config_id", payload["message"])

        response = self.url_open(
            API + "/catalog/products", data="{not json",
            headers={"Authorization": "Bearer " + self.owner_raw, "Content-Type": "application/json"},
        )
        self.assertEqual((response.status_code, json.loads(response.text)["error"]), (400, "INVALID_JSON"))

        response, payload = self._post({"config_id": self.config_a.id, **self.values()}, self.cashier_raw)
        self.assertEqual((response.status_code, payload["error"]), (403, "FORBIDDEN_SCOPE"))

        # The inventory operator may hold the scope but Odoo still requires the POS operator group.
        response, payload = self._post({"config_id": self.config_a.id, **self.values()}, self.inventory_raw)
        self.assertEqual((response.status_code, payload["error"]), (403, "FORBIDDEN"))

        response, payload = self._post({"config_id": self.config_b.id, **self.values()}, self.owner_raw)
        self.assertEqual((response.status_code, payload["error"]), (403, "FORBIDDEN"))
