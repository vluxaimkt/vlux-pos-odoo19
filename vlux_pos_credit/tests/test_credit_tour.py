from odoo.tests import tagged

from odoo.addons.pos_hr.tests.test_frontend import TestPosHrHttpCommon


@tagged("post_install", "-at_install", "vlux_e2e")
class TestVluxCreditRegister(TestPosHrHttpCommon):
    """The register side of credit sales, with employee login (pos_hr)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        credit = cls.env["pos.payment.method"]._vlux_credit_method(cls.main_pos_config.company_id)
        cls.main_pos_config.write({"payment_method_ids": [(4, credit.id)]})
        cls.env["product.template"].create({
            "name": "Refresco Crédito",
            "list_price": 18.0,
            "available_in_pos": True,
            "taxes_id": [(6, 0, [])],
        })
        cls.lupe = cls.env["res.partner"].create({
            "name": "Doña Lupe Crédito",
            "vlux_credit_allowed": True,
            "vlux_credit_limit": 30.0,
        })
        cls.no_credit = cls.env["res.partner"].create({"name": "Cliente Sin Crédito"})

    def test_credit_sales_at_the_register(self):
        self.start_pos_tour("VluxCreditRegisterTour", login="pos_admin")

        orders = self.env["pos.order"].search([("config_id", "=", self.main_pos_config.id)])
        on_credit = orders.filtered(lambda order: order.vlux_credit_amount)
        self.assertEqual(len(on_credit), 1, "only the authorised sale within the limit went through")
        self.assertEqual(on_credit.partner_id, self.lupe)
        self.assertEqual(on_credit.employee_id, self.manager2)
        self.assertFalse(on_credit.vlux_credit_flagged)
        self.assertAlmostEqual(on_credit.vlux_credit_prev_balance, 0.0)
        self.assertAlmostEqual(self.lupe.vlux_credit_balance, 18.0)
        self.assertFalse(orders.filtered(lambda order: order.partner_id == self.no_credit and order.state != "draft"))
