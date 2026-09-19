from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestVluxOwnerSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_company = cls.env["res.company"].sudo().create(
            {"name": "VLUX Owner Otra Empresa"}
        )

    def test_dashboard_metrics_are_company_scoped(self):
        service = self.env["vlux.owner.dashboard.service"].with_company(
            self.env.company
        )
        today = service._local_date()

        orders = service._orders_for_day(today)
        self.assertFalse(orders.filtered(lambda order: order.company_id != self.env.company))

    def test_dashboard_empty_metrics_are_coherent(self):
        service = self.env["vlux.owner.dashboard.service"].with_company(
            self.other_company
        )
        dashboard = service.get_dashboard()

        self.assertEqual(dashboard["summary"]["sales_today"], 0.0)
        self.assertEqual(dashboard["summary"]["tickets"], 0)
        self.assertEqual(dashboard["summary"]["average_ticket"], 0.0)
        self.assertEqual(dashboard["store"]["company_name"], self.other_company.name)

    def test_core_owner_role_preserves_dashboard_access(self):
        core_owner = self.env.ref("vlux_core.group_vlux_owner")
        legacy_owner = self.env.ref("vlux_owner.group_vlux_owner")
        user = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .sudo()
            .create(
                {
                    "name": "vlux-owner-core-compat",
                    "login": "vlux-owner-core-compat",
                    "email": "vlux-owner-core-compat@example.test",
                    "company_id": self.env.company.id,
                    "company_ids": [(6, 0, self.env.company.ids)],
                    "group_ids": [self.env.ref("base.group_user").id, core_owner.id],
                }
            )
        )

        self.assertIn(legacy_owner, core_owner.implied_ids)
        self.assertTrue(user.has_group("vlux_owner.group_vlux_owner"))

    def test_rate_limit_blocks_excess_requests(self):
        limiter = self.env["vlux.owner.rate.limit"].sudo()

        self.assertTrue(limiter.consume("test", self.env.user.id, 2, 60))
        self.assertTrue(limiter.consume("test", self.env.user.id, 2, 60))
        self.assertFalse(limiter.consume("test", self.env.user.id, 2, 60))
