from odoo.exceptions import AccessError
from odoo.fields import Command
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestVluxCoreRoles(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].sudo().create(
            {"name": "VLUX Role Test Company B"}
        )
        cls.cashier = cls._create_user("vlux-cashier", "vlux_core.group_vlux_cashier")
        cls.supervisor = cls._create_user(
            "vlux-supervisor", "vlux_core.group_vlux_supervisor"
        )
        cls.administrator = cls._create_user(
            "vlux-administrator", "vlux_core.group_vlux_administrator"
        )
        cls.owner = cls._create_user("vlux-owner", "vlux_core.group_vlux_owner")
        cls.inventory = cls._create_user(
            "vlux-inventory", "vlux_core.group_vlux_inventory_operator"
        )
        cls.auditor = cls._create_user("vlux-auditor", "vlux_core.group_vlux_auditor")
        cls.support = cls._create_user("vlux-support-roles", "vlux_core.group_vlux_support")
        cls.pos_config_a = cls.env["pos.config"].sudo().create(
            {"name": "VLUX Roles POS A", "company_id": cls.company_a.id}
        )
        company_b_env = cls.env["pos.payment.method"].with_company(cls.company_b).sudo()
        cls.company_b_payment_method = company_b_env.create(
            {
                "name": "VLUX Roles Company B Pay Later",
                "company_id": cls.company_b.id,
            }
        )
        cls.pos_config_b = cls.env["pos.config"].with_company(cls.company_b).sudo().create(
            {
                "name": "VLUX Roles POS B",
                "company_id": cls.company_b.id,
                "payment_method_ids": [Command.link(cls.company_b_payment_method.id)],
            }
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
                    "company_id": cls.company_a.id,
                    "company_ids": [(6, 0, cls.company_a.ids)],
                    "group_ids": [cls.env.ref("base.group_user").id, group.id],
                }
            )
        )

    def test_cashier_can_operate_pos_but_not_sensitive_config(self):
        self.assertTrue(self.cashier.has_group("point_of_sale.group_pos_user"))
        self.assertFalse(self.cashier.has_group("point_of_sale.group_pos_manager"))
        self.env["pos.order"].with_user(self.cashier).check_access_rights("create")

        with self.assertRaises(AccessError):
            self.pos_config_a.with_user(self.cashier).write({"name": "Nope"})

    def test_supervisor_reuses_pos_manager_without_system_admin(self):
        self.assertTrue(self.supervisor.has_group("point_of_sale.group_pos_manager"))
        self.assertFalse(self.supervisor.has_group("base.group_system"))
        self.pos_config_a.with_user(self.supervisor).write(
            {"name": "VLUX Roles POS A Supervisor"}
        )

    def test_administrator_and_owner_are_functional_not_system_admins(self):
        for user in (self.administrator, self.owner):
            self.assertTrue(user.has_group("point_of_sale.group_pos_manager"))
            self.assertTrue(user.has_group("stock.group_stock_manager"))
            self.assertTrue(user.has_group("product.group_product_manager"))
            self.assertFalse(user.has_group("base.group_system"))

    def test_inventory_operator_stock_allowed_pos_denied(self):
        self.assertTrue(self.inventory.has_group("stock.group_stock_user"))
        self.assertFalse(self.inventory.has_group("point_of_sale.group_pos_user"))
        self.env["stock.picking"].with_user(self.inventory).check_access_rights("create")
        with self.assertRaises(AccessError):
            self.env["pos.order"].with_user(self.inventory).check_access_rights("create")

    def test_auditor_is_read_only_on_critical_models(self):
        auditor_order_model = self.env["pos.order"].with_user(self.auditor)
        auditor_order_model.check_access_rights("read")
        for operation in ("create", "write", "unlink"):
            with self.assertRaises(AccessError):
                auditor_order_model.check_access_rights(operation)

    def test_support_can_diagnose_but_not_operate_business(self):
        info = self.env["vlux.core.system.info"].with_user(self.support).get_safe_info()
        self.assertEqual(info["edition"], "local_core")
        with self.assertRaises(AccessError):
            self.env["pos.order"].with_user(self.support).check_access_rights("create")

    def test_multi_company_rules_keep_company_b_out(self):
        visible = self.env["pos.config"].with_user(self.cashier).search(
            [("id", "in", [self.pos_config_a.id, self.pos_config_b.id])]
        )
        self.assertIn(self.pos_config_a, visible)
        self.assertNotIn(self.pos_config_b, visible)
