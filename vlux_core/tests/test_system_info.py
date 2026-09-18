from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestVluxCoreSystemInfo(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.support_user = cls._create_user(
            "vlux-support",
            "vlux_core.group_vlux_support",
        )
        cls.cashier_user = cls._create_user(
            "vlux-cashier-info",
            "vlux_core.group_vlux_cashier",
        )

    @classmethod
    def _create_user(cls, login, group_xmlid):
        group = cls.env.ref(group_xmlid)
        return (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .sudo()
            .create(
                {
                    "name": login,
                    "login": login,
                    "email": f"{login}@example.test",
                    "company_id": cls.env.company.id,
                    "company_ids": [(6, 0, cls.env.company.ids)],
                    "group_ids": [cls.env.ref("base.group_user").id, group.id],
                }
            )
        )

    def test_health_status_is_minimal(self):
        self.assertEqual(
            self.env["vlux.core.system.info"].health_status(),
            {"status": "ok"},
        )

    def test_support_can_read_safe_system_info(self):
        info = self.env["vlux.core.system.info"].with_user(
            self.support_user
        ).get_safe_info()

        self.assertEqual(info["vlux_core_version"], "19.0.1.1.0")
        self.assertEqual(info["edition"], "local_core")
        self.assertIn("addons", info)
        self.assertNotIn("database", info)
        self.assertNotIn("paths", info)
        self.assertNotIn("password", str(info).lower())
        self.assertIn(info["ready"]["status"], {"ready", "not_ready"})
        self.assertIn("database", info["ready"]["checks"])

    def test_non_support_cannot_read_system_info(self):
        with self.assertRaises(AccessError):
            self.env["vlux.core.system.info"].with_user(
                self.cashier_user
            ).get_safe_info()
