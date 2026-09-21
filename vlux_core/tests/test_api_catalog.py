import json
import time
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import HttpCase, TransactionCase, tagged

from odoo.addons.vlux_core.controllers import api_catalog

from .test_api_v1 import API, VluxApiCase


@tagged("post_install", "-at_install")
class TestCatalogSyncModel(TransactionCase):
    """The sync stamp and the tombstones are what make incremental sync honest."""

    def _template(self, name, **values):
        return self.env["product.template"].create({"name": name, "list_price": 10.0, **values})

    def test_template_write_moves_every_variant_and_variant_write_only_itself(self):
        attribute = self.env["product.attribute"].create({
            "name": "Talla", "value_ids": [(0, 0, {"name": "S"}), (0, 0, {"name": "M"})],
        })
        template = self._template("Playera", attribute_line_ids=[
            (0, 0, {"attribute_id": attribute.id, "value_ids": [(6, 0, attribute.value_ids.ids)]}),
        ])
        small, medium = template.product_variant_ids
        old = fields.Datetime.now() - timedelta(hours=1)
        self.env.cr.execute("UPDATE product_product SET vlux_sync_date = %s WHERE id IN %s", (old, tuple(template.product_variant_ids.ids)))
        template.product_variant_ids.invalidate_recordset(["vlux_sync_date"])

        template.list_price = 12.0
        self.assertGreater(small.vlux_sync_date, old)
        self.assertGreater(medium.vlux_sync_date, old)

        self.env.cr.execute("UPDATE product_product SET vlux_sync_date = %s WHERE id IN %s", (old, tuple(template.product_variant_ids.ids)))
        template.product_variant_ids.invalidate_recordset(["vlux_sync_date"])
        small.barcode = "7500000000001"
        self.assertGreater(small.vlux_sync_date, old)
        self.assertEqual(medium.vlux_sync_date, old)

    def test_deleting_a_variant_or_a_template_leaves_tombstones(self):
        Tombstone = self.env["vlux.catalog.tombstone"]
        template = self._template("Efímero")
        variant = template.product_variant_id
        variant_id = variant.id

        variant.unlink()
        stone = Tombstone.search([("model", "=", "product.product"), ("res_id", "=", variant_id)])
        self.assertEqual(len(stone), 1)
        self.assertEqual(stone.company_id, self.env["res.company"])

        attribute = self.env["product.attribute"].create({
            "name": "Color", "value_ids": [(0, 0, {"name": "Rojo"}), (0, 0, {"name": "Azul"})],
        })
        template = self._template("Gorra", company_id=self.env.company.id, attribute_line_ids=[
            (0, 0, {"attribute_id": attribute.id, "value_ids": [(6, 0, attribute.value_ids.ids)]}),
        ])
        ids = template.product_variant_ids.ids
        template.product_variant_ids[0].action_archive()  # archived variants are deleted too
        template.unlink()
        stones = Tombstone.search([("model", "=", "product.product"), ("res_id", "in", ids)])
        self.assertEqual(len(stones), 2)
        self.assertEqual(stones.mapped("company_id"), self.env.company)

    def test_deleting_a_partner_leaves_a_tombstone_and_recording_is_idempotent(self):
        Tombstone = self.env["vlux.catalog.tombstone"]
        partner = self.env["res.partner"].create({"name": "Cliente breve"})
        partner_id = partner.id
        Tombstone._record(partner)
        Tombstone._record(partner)
        partner.unlink()
        self.assertEqual(Tombstone.search_count([("model", "=", "res.partner"), ("res_id", "=", partner_id)]), 1)

    def test_old_tombstones_are_garbage_collected(self):
        Tombstone = self.env["vlux.catalog.tombstone"]
        stale = Tombstone.create({"model": "product.product", "res_id": 999999,
                                  "deleted_at": fields.Datetime.now() - timedelta(days=91)})
        fresh = Tombstone.create({"model": "product.product", "res_id": 999998})
        Tombstone._gc_old_tombstones()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())


@tagged("post_install", "-at_install")
class TestVluxApiCatalog(HttpCase, VluxApiCase):
    """A register syncs the catalog by pages and only ever receives what changed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.env["res.company"].sudo().create({"name": "VLUX API Company B"})
        cls.owner = cls._make_user("api-catalog-owner", "vlux_core.group_vlux_owner")
        cls.token, cls.raw = cls.env["vlux.api.token"].issue(
            "Caja catálogo", "catalog:read system:read", user=cls.owner
        )
        cls.no_scope_token, cls.raw_no_scope = cls.env["vlux.api.token"].issue(
            "Sin catálogo", "system:read", user=cls.owner
        )
        cls.env.flush_all()

    def setUp(self):
        super().setUp()
        # The settle window exists for concurrent writers; a test has none.
        patcher = patch.object(api_catalog, "SETTLE_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _next_second():
        """Stamps have one-second precision and a page stops before the
        current second: wait for the clock to move on before syncing."""
        second = datetime.now().second
        while datetime.now().second == second:
            time.sleep(0.05)

    def _get(self, path, token=None, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items() if value is not None)
        url = API + path + ("?" + query if query else "")
        headers = {"Authorization": "Bearer " + (token or self.raw)}
        response = self.url_open(url, headers=headers)
        return response, json.loads(response.text)

    def _products(self, names, **values):
        return [
            self.env["product.template"].create({"name": name, "list_price": 5.0, "available_in_pos": True, **values})
            for name in names
        ]

    def _sync_all(self, path="/catalog/products", limit=2):
        """Page through the feed from scratch; return (items, cursor, pages)."""
        self._next_second()
        items, cursor, pages = [], None, 0
        while True:
            response, body = self._get(path, cursor=cursor, limit=limit)
            self.assertEqual(response.status_code, 200, body)
            items += body["data"]["items"]
            cursor = body["data"]["next_cursor"]
            pages += 1
            if not body["data"]["has_more"]:
                return items, cursor, pages

    def test_full_sync_pages_without_gaps_or_duplicates(self):
        templates = self._products(["Sync A", "Sync B", "Sync C", "Sync D", "Sync E"])
        expected = {template.product_variant_id.id for template in templates}
        self.env.flush_all()

        items, cursor, pages = self._sync_all(limit=2)

        ids = [item["id"] for item in items]
        self.assertEqual(len(ids), len(set(ids)), "no product may be delivered twice")
        self.assertTrue(expected <= set(ids))
        self.assertGreaterEqual(pages, 3)
        stamps = [(item["sync_date"], item["id"]) for item in items]
        self.assertEqual(stamps, sorted(stamps), "the feed is ordered by (sync_date, id)")
        item = next(i for i in items if i["id"] == templates[0].product_variant_id.id)
        self.assertEqual(item["name"], "Sync A")
        self.assertEqual(item["list_price"], 5.0)
        self.assertTrue(item["available_in_pos"])
        self.assertTrue(item["active"])
        self.assertIn("tax_ids", item)

        response, body = self._get("/catalog/products", cursor=cursor)
        self.assertEqual(body["data"]["items"], [])
        self.assertFalse(body["data"]["has_more"])

    def test_incremental_sync_delivers_changes_flags_and_deletions_once(self):
        renamed, delisted, untouched = self._products(["Inc renombrado", "Inc retirado", "Inc intacto"])
        # Odoo refuses to archive or delete POS products while a session is open.
        archived, deleted = self._products(["Inc archivado", "Inc borrado"], available_in_pos=False)
        self.env.flush_all()
        _items, cursor, _pages = self._sync_all(limit=50)

        # product_variant_id only points at active variants: keep the ids first.
        renamed_id, archived_id, delisted_id, deleted_id = (
            t.product_variant_id.id for t in (renamed, archived, delisted, deleted)
        )
        renamed.name = "Inc renombrado v2"
        archived.action_archive()
        delisted.available_in_pos = False
        deleted.unlink()
        self.env.flush_all()

        self._next_second()
        response, body = self._get("/catalog/products", cursor=cursor)
        data = body["data"]
        by_id = {item["id"]: item for item in data["items"]}
        self.assertEqual(
            set(by_id), {renamed_id, archived_id, delisted_id},
            "only the changed products come back; the untouched one does not",
        )
        self.assertEqual(by_id[renamed_id]["name"], "Inc renombrado v2")
        self.assertFalse(by_id[archived_id]["active"])
        self.assertFalse(by_id[delisted_id]["available_in_pos"])
        self.assertEqual(data["deleted"], [deleted_id])
        self.assertFalse(data["has_more"])

        # Retrying the same page is harmless; moving on delivers nothing twice.
        response, again = self._get("/catalog/products", cursor=cursor)
        self.assertEqual(again["data"]["items"], data["items"])
        self.assertEqual(again["data"]["deleted"], [deleted_id])
        response, after = self._get("/catalog/products", cursor=data["next_cursor"])
        self.assertEqual((after["data"]["items"], after["data"]["deleted"]), ([], []))

    def test_cursor_is_validated(self):
        response, body = self._get("/catalog/products", cursor="not-a-cursor")
        self.assertEqual((response.status_code, body["error"]), (400, "INVALID_CURSOR"))

        too_old = api_catalog.encode_cursor(fields.Datetime.now() - timedelta(days=120), 0)
        response, body = self._get("/catalog/products", cursor=too_old)
        self.assertEqual((response.status_code, body["error"]), (409, "RESYNC_REQUIRED"))

        response, body = self._get("/catalog/products", limit="abc")
        self.assertEqual((response.status_code, body["error"]), (400, "VALIDATION_ERROR"))

    def test_scope_and_company_are_enforced(self):
        response, body = self._get("/catalog/products", token=self.raw_no_scope)
        self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN_SCOPE"))

        foreign = self.env["product.template"].sudo().create({
            "name": "Producto de otra compañía", "company_id": self.company_b.id, "list_price": 1.0,
        })
        self.env.flush_all()
        items, _cursor, _pages = self._sync_all(limit=100)
        self.assertNotIn(foreign.product_variant_id.id, [item["id"] for item in items])

    def test_customers_feed_with_deletion(self):
        partner = self.env["res.partner"].create({"name": "Cliente API", "email": "cliente@example.test"})
        self.env.flush_all()
        items, cursor, _pages = self._sync_all("/catalog/customers", limit=100)
        record = next(item for item in items if item["id"] == partner.id)
        self.assertEqual((record["name"], record["email"]), ("Cliente API", "cliente@example.test"))

        partner_id = partner.id
        partner.unlink()
        self.env.flush_all()
        self._next_second()
        response, body = self._get("/catalog/customers", cursor=cursor)
        self.assertEqual(body["data"]["deleted"], [partner_id])

    def test_snapshots_and_store_config(self):
        tax = self.env["account.tax"].sudo().create({
            "name": "IVA API 16", "amount": 16, "type_tax_use": "sale", "company_id": self.env.company.id,
        })
        category = self.env["pos.category"].sudo().create({"name": "API Bebidas"})
        pricelist = self.env["product.pricelist"].sudo().create({
            "name": "API Mayoreo", "company_id": self.env.company.id,
            "item_ids": [(0, 0, {"applied_on": "3_global", "compute_price": "percentage", "percent_price": 10})],
        })
        config = self.env["pos.config"].sudo().create({"name": "API Caja 1", "company_id": self.env.company.id})
        self.env.flush_all()

        response, body = self._get("/catalog/taxes")
        self.assertIn(tax.id, [item["id"] for item in body["data"]["items"]])
        response, body = self._get("/catalog/pos-categories")
        self.assertIn(category.id, [item["id"] for item in body["data"]["items"]])
        response, body = self._get("/catalog/categories")
        self.assertTrue(body["data"]["items"])
        response, body = self._get("/catalog/pricelists")
        found = next(item for item in body["data"]["items"] if item["id"] == pricelist.id)
        self.assertEqual(found["items"][0]["percent_price"], 10)

        response, body = self._get("/store/config")
        self.assertEqual(response.status_code, 200)
        data = body["data"]
        self.assertEqual(data["company"]["id"], self.env.company.id)
        self.assertEqual(data["currency"]["name"], self.env.company.currency_id.name)
        register = next(item for item in data["registers"] if item["id"] == config.id)
        self.assertEqual(register["name"], "API Caja 1")
        self.assertIn("payment_methods", register)

    def test_openapi_lists_the_catalog_endpoints(self):
        response = self.url_open(API + "/openapi.json")
        paths = json.loads(response.text)["data"]["paths"]
        for path in ("/catalog/products", "/catalog/customers", "/catalog/taxes", "/catalog/pricelists", "/store/config"):
            self.assertIn(path, paths)
        self.assertEqual(paths["/catalog/products"]["get"]["x-scope"], "catalog:read")
