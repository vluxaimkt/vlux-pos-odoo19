import base64
import io

from odoo.fields import Command
from odoo.tests.common import TransactionCase


def png_bytes(size=(160, 120), color=(200, 30, 30)):
    from PIL import Image

    image = Image.new("RGB", size, color)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class VluxCatalogCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].sudo().create({"name": "VLUX Catalog Company B"})
        cls.config_a = cls.env["pos.config"].sudo().create(
            {"name": "VLUX Catalog POS A", "company_id": cls.company_a.id}
        )
        cls.session_a = cls.env["pos.session"].sudo().create(
            {"config_id": cls.config_a.id, "user_id": cls.env.user.id}
        )
        payment_b = (
            cls.env["pos.payment.method"]
            .with_company(cls.company_b)
            .sudo()
            .create({"name": "VLUX Catalog B Pay Later", "company_id": cls.company_b.id})
        )
        cls.config_b = cls.env["pos.config"].with_company(cls.company_b).sudo().create(
            {
                "name": "VLUX Catalog POS B",
                "company_id": cls.company_b.id,
                "payment_method_ids": [Command.link(payment_b.id)],
            }
        )
        cls.session_b = cls.env["pos.session"].with_company(cls.company_b).sudo().create(
            {"config_id": cls.config_b.id, "user_id": cls.env.user.id}
        )

        cls.owner = cls._make_user("catalog-owner", ["vlux_core.group_vlux_owner"])
        cls.administrator = cls._make_user("catalog-admin", ["vlux_core.group_vlux_administrator"])
        cls.cashier = cls._make_user("catalog-cashier", ["vlux_core.group_vlux_cashier"])
        cls.cashier_quick = cls._make_user(
            "catalog-cashier-quick",
            ["vlux_core.group_vlux_cashier", "vlux_pos_catalog.group_vlux_catalog_quick_create"],
        )
        cls.inventory = cls._make_user("catalog-inventory", ["vlux_core.group_vlux_inventory_operator"])
        cls.inventory_cashier = cls._make_user(
            "catalog-inventory-cashier",
            ["vlux_core.group_vlux_inventory_operator", "vlux_core.group_vlux_cashier"],
        )
        cls.auditor = cls._make_user("catalog-auditor", ["vlux_core.group_vlux_auditor"])
        cls.owner_b = cls._make_user("catalog-owner-b", ["vlux_core.group_vlux_owner"], company=cls.company_b)

    @classmethod
    def _make_user(cls, login, group_xmlids, company=None):
        company = company or cls.company_a
        groups = [cls.env.ref("base.group_user").id] + [cls.env.ref(xmlid).id for xmlid in group_xmlids]
        return (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .sudo()
            .create(
                {
                    "name": login,
                    "login": login,
                    "email": f"{login}@example.test",
                    "company_id": company.id,
                    "company_ids": [(6, 0, company.ids)],
                    "group_ids": [(6, 0, groups)],
                }
            )
        )

    def quick_create(self, user, values, config=None):
        config = config or self.config_a
        return (
            self.env["product.template"]
            .with_user(user)
            .with_company(user.company_id)
            .vlux_pos_quick_create(values, config.id)
        )

    @staticmethod
    def image_b64():
        return base64.b64encode(png_bytes()).decode("ascii")

    def values(self, **overrides):
        base = {"barcode": "7501000000017", "name": "Refresco Cola 600 ml", "list_price": 18.5}
        base.update(overrides)
        return base
