from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import tagged

from .common import VluxCatalogCase


@tagged("post_install", "-at_install")
class TestVluxCatalogQuickCreate(VluxCatalogCase):
    # ------------------------------------------------------------------
    # authorisation
    # ------------------------------------------------------------------

    def test_permission_is_asked_to_the_server_for_the_logged_in_user(self):
        Template = self.env["product.template"]
        self.assertTrue(Template.with_user(self.owner).vlux_pos_quick_create_allowed())
        self.assertTrue(Template.with_user(self.administrator).vlux_pos_quick_create_allowed())
        self.assertTrue(Template.with_user(self.cashier_quick).vlux_pos_quick_create_allowed())
        self.assertFalse(Template.with_user(self.cashier).vlux_pos_quick_create_allowed())
        self.assertFalse(Template.with_user(self.auditor).vlux_pos_quick_create_allowed())

    def test_quick_create_permission_is_its_own_privilege(self):
        """It must render as a checkbox next to the VLUX role, not as a role."""
        group = self.env.ref("vlux_pos_catalog.group_vlux_catalog_quick_create")
        role = self.env.ref("vlux_core.group_vlux_cashier")
        self.assertNotEqual(group.privilege_id, role.privilege_id)
        self.assertEqual(len(group.privilege_id.group_ids), 1)

    def test_owner_and_administrator_can_create(self):
        for index, user in enumerate((self.owner, self.administrator)):
            result = self.quick_create(user, self.values(barcode=f"750100000010{index}"))
            self.assertTrue(result["ok"], result)
            product = self.env["product.product"].browse(result["product_id"])
            self.assertEqual(product.barcode, f"750100000010{index}")

    def test_cashier_without_explicit_permission_is_denied(self):
        self.assertFalse(self.cashier.vlux_catalog_can_quick_create)
        with self.assertRaises(AccessError):
            self.quick_create(self.cashier, self.values())
        self.assertFalse(self.env["product.product"].search([("barcode", "=", "7501000000017")]))

    def test_cashier_with_explicit_permission_can_create(self):
        self.assertTrue(self.cashier_quick.vlux_catalog_can_quick_create)
        # Still not a product manager: direct product creation stays forbidden.
        with self.assertRaises(AccessError):
            self.env["product.template"].with_user(self.cashier_quick).create({"name": "Nope"})
        result = self.quick_create(self.cashier_quick, self.values())
        self.assertTrue(result["ok"])

    def test_inventory_operator_needs_pos_access(self):
        self.assertTrue(self.inventory.vlux_catalog_can_quick_create)
        with self.assertRaises(AccessError):
            self.quick_create(self.inventory, self.values())
        result = self.quick_create(self.inventory_cashier, self.values(barcode="7501000000024"))
        self.assertTrue(result["ok"])

    def test_auditor_is_denied(self):
        with self.assertRaises(AccessError):
            self.quick_create(self.auditor, self.values())

    def test_permission_flag_is_loaded_in_pos_data(self):
        fields = self.env["res.users"]._load_pos_data_fields(self.config_a)
        self.assertIn("vlux_catalog_can_quick_create", fields)
        rows = (
            self.env["res.users"]
            .with_user(self.owner)
            ._load_pos_data_read(self.owner, self.config_a)
        )
        self.assertTrue(rows[0]["vlux_catalog_can_quick_create"])

    def test_closed_session_blocks_creation(self):
        config = self.env["pos.config"].sudo().create({"name": "VLUX Catalog closed", "company_id": self.company_a.id})
        with self.assertRaises(UserError):
            self.quick_create(self.owner, self.values(), config=config)

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------

    def test_name_is_required(self):
        with self.assertRaises(ValidationError):
            self.quick_create(self.owner, self.values(name="   "))

    def test_price_is_validated(self):
        for bad in (-1, "abc", float("nan"), float("inf"), 1e12):
            with self.subTest(price=bad), self.assertRaises(ValidationError):
                self.quick_create(self.owner, self.values(list_price=bad))
        result = self.quick_create(self.owner, self.values(list_price="0"))
        self.assertTrue(result["ok"])

    def test_barcode_is_validated(self):
        for bad in ("", "12", "x" * 65, "abc def", "750\x1d1"):
            with self.subTest(barcode=bad), self.assertRaises(ValidationError):
                self.quick_create(self.owner, self.values(barcode=bad))

    def test_foreign_keys_are_validated(self):
        with self.assertRaises(ValidationError):
            self.quick_create(self.owner, self.values(pos_categ_id=999999))
        with self.assertRaises(ValidationError):
            self.quick_create(self.owner, self.values(taxes_ids=[999999]))
        purchase_tax = self.env["account.tax"].sudo().create(
            {"name": "VLUX purchase tax", "amount": 8, "type_tax_use": "purchase", "company_id": self.company_a.id}
        )
        with self.assertRaises(ValidationError):
            self.quick_create(self.owner, self.values(taxes_ids=[purchase_tax.id]))

    # ------------------------------------------------------------------
    # result
    # ------------------------------------------------------------------

    def test_created_product_is_available_in_pos_and_company_scoped(self):
        categ = self.env["pos.category"].sudo().create({"name": "VLUX Catalog Bebidas"})
        result = self.quick_create(
            self.owner,
            self.values(pos_categ_id=categ.id, default_code="REF-1", initial_qty=0, taxes_ids=[]),
        )
        template = self.env["product.template"].browse(result["product_tmpl_id"])
        self.assertTrue(template.available_in_pos)
        self.assertTrue(template.sale_ok)
        self.assertEqual(template.company_id, self.company_a)
        self.assertEqual(template.pos_categ_ids, categ)
        self.assertEqual(template.default_code, "REF-1")
        self.assertEqual(template.list_price, 18.5)
        self.assertFalse(template.taxes_id)
        # The exact call the POS makes to load the new product into the session.
        loaded = self.env["product.template"].with_user(self.owner).load_product_from_pos(
            self.config_a.id, [("id", "=", template.id)]
        )
        self.assertEqual([row["id"] for row in loaded["product.template"]], [template.id])
        self.assertEqual(loaded["product.product"][0]["barcode"], "7501000000017")

    def test_default_taxes_apply_when_not_specified(self):
        tax = self.env["account.tax"].sudo().create(
            {"name": "VLUX sale 16", "amount": 16, "type_tax_use": "sale", "company_id": self.company_a.id}
        )
        self.company_a.sudo().write({"account_sale_tax_id": tax.id})
        defaults = self.env["product.template"].with_user(self.owner).vlux_pos_quick_create_defaults(self.config_a.id)
        self.assertEqual(defaults["taxes_ids"], [tax.id])
        result = self.quick_create(self.owner, self.values())
        template = self.env["product.template"].browse(result["product_tmpl_id"])
        self.assertEqual(template.taxes_id, tax)

    def test_created_product_can_be_sold(self):
        result = self.quick_create(self.owner, self.values())
        product = self.env["product.product"].browse(result["product_id"])
        order = self.env["pos.order"].with_user(self.owner).create({
            "session_id": self.session_a.id,
            "company_id": self.company_a.id,
            "amount_tax": 0.0,
            "amount_total": 37.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
            "lines": [(0, 0, {
                "name": "line",
                "product_id": product.id,
                "qty": 2,
                "price_unit": 18.5,
                "price_subtotal": 37.0,
                "price_subtotal_incl": 37.0,
            })],
        })
        self.assertEqual(order.lines.product_id, product)
        self.assertEqual(sum(order.lines.mapped("qty")), 2)

    def test_initial_stock_is_applied(self):
        result = self.quick_create(self.owner, self.values(initial_qty=5, is_storable=True))
        product = self.env["product.product"].browse(result["product_id"])
        self.assertTrue(product.is_storable)
        self.assertEqual(product.with_company(self.company_a).qty_available, 5)

    def test_manual_image_lands_in_odoo_filestore(self):
        result = self.quick_create(self.owner, self.values(image=self.image_b64()))
        template = self.env["product.template"].browse(result["product_tmpl_id"])
        self.assertTrue(template.image_1920)
        self.assertTrue(template.image_128)
        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "product.template"),
            ("res_id", "=", template.id),
            ("res_field", "=", "image_1920"),
        ])
        self.assertEqual(len(attachment), 1)
        self.assertTrue(attachment.store_fname, "image must be stored in the filestore, not inline")

    def test_invalid_image_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.quick_create(self.owner, self.values(image="bm90IGFuIGltYWdl"))
        with self.assertRaises(ValidationError):
            self.quick_create(self.owner, self.values(image="%%%not-base64%%%"))

    # ------------------------------------------------------------------
    # duplicates
    # ------------------------------------------------------------------

    def test_duplicate_barcode_is_reported_not_created(self):
        first = self.quick_create(self.owner, self.values())
        second = self.quick_create(self.owner, self.values(name="Otro"))
        self.assertFalse(second["ok"])
        self.assertEqual(second["code"], "BARCODE_EXISTS")
        self.assertEqual(second["existing"]["id"], first["product_id"])
        self.assertEqual(self.env["product.product"].search_count([("barcode", "=", "7501000000017")]), 1)

    def test_existing_archived_product_can_be_enabled(self):
        template = self.env["product.template"].sudo().create({
            "name": "Archivado", "barcode": "7501000000031", "available_in_pos": False, "sale_ok": False,
            "company_id": self.company_a.id,
        })
        template.action_archive()
        attempt = self.quick_create(self.owner, self.values(barcode="7501000000031"))
        self.assertEqual(attempt["code"], "BARCODE_EXISTS")
        self.assertFalse(attempt["existing"]["active"])
        result = self.env["product.template"].with_user(self.owner).vlux_pos_enable_existing(
            attempt["existing"]["id"], self.config_a.id
        )
        template.invalidate_recordset()
        self.assertTrue(result["ok"])
        self.assertTrue(template.active)
        self.assertTrue(template.available_in_pos)
        self.assertTrue(template.sale_ok)

    def test_audit_finds_duplicates_without_constraint(self):
        Product = self.env["product.template"]
        self.assertEqual(Product.vlux_audit_duplicate_barcodes(self.company_a), [])
        a = self.quick_create(self.owner, self.values(barcode="7501000000048"))
        b = self.quick_create(self.owner, self.values(barcode="7501000000055"))
        # Simulate legacy data that bypassed the Python constraint.
        self.env.cr.execute(
            "UPDATE product_product SET barcode = %s WHERE id = %s", ["7501000000048", b["product_id"]]
        )
        self.env["product.product"].invalidate_model()
        audit = Product.vlux_audit_duplicate_barcodes(self.company_a)
        self.assertEqual(audit, [{"barcode": "7501000000048", "count": 2}])
        self.assertTrue(a["ok"])

    # ------------------------------------------------------------------
    # multi-company
    # ------------------------------------------------------------------

    def test_multi_company_isolation(self):
        with self.assertRaises(AccessError):
            self.quick_create(self.owner_b, self.values(), config=self.config_a)
        result = self.quick_create(self.owner, self.values())
        template = self.env["product.template"].browse(result["product_tmpl_id"])
        domain_b = self.env["product.template"]._load_pos_data_domain({}, self.config_b)
        self.assertFalse(self.env["product.template"].sudo().search(domain_b + [("id", "=", template.id)]))
        domain_a = self.env["product.template"]._load_pos_data_domain({}, self.config_a)
        self.assertTrue(self.env["product.template"].sudo().search(domain_a + [("id", "=", template.id)]))
        # Same barcode in company B is a different product, not a duplicate.
        result_b = self.quick_create(self.owner_b, self.values(), config=self.config_b)
        self.assertTrue(result_b["ok"])
        self.assertNotEqual(result_b["product_id"], result["product_id"])
