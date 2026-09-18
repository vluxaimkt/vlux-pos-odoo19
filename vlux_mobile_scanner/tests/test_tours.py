from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged("post_install", "-at_install", "vlux_e2e")
class TestVluxMobileScannerTours(TestPointOfSaleHttpCommon):
    """Browser end-to-end of the scanner protocol: simulated phone + real POS,
    both bus hops (POS notification and phone push) included."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.template"].create({
            "name": "Agua Scanner 1L",
            "barcode": "7509992000019",
            "list_price": 12.0,
            "available_in_pos": True,
            "sale_ok": True,
            "taxes_id": [(6, 0, [])],
        })
        cls.pos_user.group_ids += cls.env.ref("vlux_core.group_vlux_cashier")

    def _events(self):
        return self.env["vlux.mobile.scanner.event"].sudo().search(
            [("pos_config_id", "=", self.main_pos_config.id)], order="id"
        )

    def test_known_barcode_is_delivered_to_cart(self):
        self.start_pos_tour("VluxScannerKnownBarcodeTour", login="pos_user")
        events = self._events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events.state, "delivered")
        self.assertEqual(events.product_id, self.product.product_variant_id)
        self.assertEqual(events.unit_price, 12.0)

    def test_results_are_pushed_to_the_phone(self):
        self.start_pos_tour("VluxScannerPushDeliveryTour", login="pos_user")
        events = self._events()
        self.assertEqual(len(events), 3)
        self.assertEqual(set(events.mapped("state")), {"delivered"})

    def test_burst_of_ten_scans_all_delivered(self):
        self.start_pos_tour("VluxScannerBurstTour", login="pos_user")
        events = self._events()
        self.assertEqual(len(events), 10)
        self.assertEqual(set(events.mapped("state")), {"delivered"})
        self.assertEqual(len(set(events.mapped("request_id"))), 10)

    def test_repeated_scan_adds_five_units_without_ghost_duplicates(self):
        self.start_pos_tour("VluxScannerRepeatedBarcodeTour", login="pos_user")
        events = self._events()
        self.assertEqual(len(events), 5)
        self.assertEqual(set(events.mapped("state")), {"delivered"})
        self.assertEqual(len(set(events.mapped("request_id"))), 5)

    def test_unknown_barcode_is_reported_not_found(self):
        self.start_pos_tour("VluxScannerUnknownBarcodeTour", login="pos_user")
        events = self._events()
        self.assertEqual(events.state, "not_found")
        self.assertIn(events.result_code, ("NO_ORDER_CHANGE", "REGISTER_PROMPTED"))

    def test_pos_not_on_product_screen_reports_not_ready(self):
        self.start_pos_tour("VluxScannerPosNotReadyTour", login="pos_user")
        events = self._events()
        self.assertEqual(events.state, "failed")
        self.assertEqual(events.result_code, "POS_NOT_READY")

    def test_duplicate_bus_delivery_is_processed_once(self):
        self.start_pos_tour("VluxScannerDuplicateDeliveryTour", login="pos_user")
        events = self._events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events.state, "delivered")
