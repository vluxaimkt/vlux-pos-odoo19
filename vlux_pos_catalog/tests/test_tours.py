from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged("post_install", "-at_install", "vlux_e2e")
class TestVluxCatalogTours(TestPointOfSaleHttpCommon):
    """Browser end-to-end flows. Tagged ``vlux_e2e`` so the fast CI job can skip
    them and the E2E job runs them with a real Chromium."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["product.template"].create({
            "name": "Agua Tour 1L",
            "barcode": "7509990000011",
            "list_price": 12.0,
            "available_in_pos": True,
            "sale_ok": True,
            "taxes_id": [(6, 0, [])],
        })
        # Known to the catalog, not to the register: scanning it opens the quick
        # form and saving must not create a second product.
        cls.env["product.template"].create({
            "name": "Refresco Duplicado",
            "barcode": "7509990000042",
            "list_price": 15.0,
            "available_in_pos": False,
            "sale_ok": True,
            "taxes_id": [(6, 0, [])],
        })
        cls.pos_admin.group_ids += cls.env.ref("vlux_core.group_vlux_administrator")
        cls.pos_user.group_ids += cls.env.ref("vlux_core.group_vlux_cashier")

    def test_known_barcode_adds_to_cart(self):
        self.start_pos_tour("VluxCatalogKnownBarcodeTour", login="pos_admin")

    def test_repeated_scan_increments_quantity(self):
        self.start_pos_tour("VluxCatalogRepeatedScanTour", login="pos_admin")

    def test_unknown_barcode_quick_create_adds_to_cart(self):
        self.start_pos_tour("VluxCatalogUnknownBarcodeTour", login="pos_admin")
        product = self.env["product.product"].search([("barcode", "=", "7509990000028")])
        self.assertEqual(len(product), 1)
        self.assertEqual(product.name, "Producto Nuevo Tour")
        self.assertEqual(product.list_price, 42.5)
        self.assertTrue(product.available_in_pos)
        self.assertEqual(product.company_id, self.env.company)

    def test_unknown_barcode_with_manual_photo(self):
        self.start_pos_tour("VluxCatalogUnknownBarcodeWithPhotoTour", login="pos_admin")
        template = self.env["product.template"].search([("barcode", "=", "7509990000035")])
        self.assertTrue(template.image_1920)
        attachment = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "product.template"),
            ("res_id", "=", template.id),
            ("res_field", "=", "image_1920"),
        ])
        self.assertTrue(attachment.store_fname)

    def test_duplicate_barcode_reuses_the_existing_product(self):
        self.start_pos_tour("VluxCatalogDuplicateBarcodeTour", login="pos_admin")
        products = self.env["product.product"].with_context(active_test=False).search([("barcode", "=", "7509990000042")])
        self.assertEqual(len(products), 1, "a duplicate barcode must never create a second product")
        self.assertEqual(products.name, "Refresco Duplicado")
        self.assertTrue(products.available_in_pos, "using the existing product puts it back on sale in the POS")

    def test_cashier_without_permission_gets_standard_not_found(self):
        self.start_pos_tour("VluxCatalogDeniedTour", login="pos_user")
        self.assertFalse(self.env["product.product"].search([("barcode", "=", "7509990000028")]))
