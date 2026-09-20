from odoo.fields import Command
from odoo.tests.common import HttpCase, TransactionCase, tagged


class VluxFiscalCase:
    @classmethod
    def _prepare_company(cls, env):
        company = env.company
        company.sudo().write({
            "vat": "VLX010101AAA",
            "vlux_fiscal_regime": "601 - General de Ley Personas Morales",
        })
        return company


@tagged("post_install", "-at_install")
class TestVluxCompanyFiscalFields(TransactionCase, VluxFiscalCase):
    def test_receipt_legend_has_a_default(self):
        company = self.env["res.company"].sudo().create({"name": "VLUX Recibo Co"})

        self.assertEqual(company.vlux_receipt_legend, "Este ticket no es un comprobante fiscal")
        self.assertFalse(company.vlux_fiscal_regime, "the regime is filled in per company")

    def test_fields_reach_the_pos(self):
        fields = self.env["res.company"]._load_pos_data_fields(self.env["pos.config"])

        self.assertIn("vlux_fiscal_regime", fields)
        self.assertIn("vlux_receipt_legend", fields)
        # The fields Odoo already prints must still be there.
        for field in ("vat", "street", "city", "zip", "phone"):
            self.assertIn(field, fields)


@tagged("post_install", "-at_install", "vlux_e2e")
class TestVluxReceiptTour(HttpCase, VluxFiscalCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls._prepare_company(cls.env)
        category = cls.env["pos.category"].sudo().create({"name": "VLUX Recibo"})
        cls.env["product.template"].sudo().create({
            "name": "VLUX Recibo Producto",
            "available_in_pos": True,
            "list_price": 100.0,
            "taxes_id": [Command.clear()],
            "pos_categ_ids": [Command.set(category.ids)],
        })
        # Its own journal: Odoo refuses two cash payment methods on the same one.
        cash_journal = cls.env["account.journal"].sudo().create({
            "name": "VLUX Recibo Caja",
            "type": "cash",
            "code": "VRCJ",
            "company_id": company.id,
        })
        payment = cls.env["pos.payment.method"].sudo().create({
            "name": "VLUX Recibo Efectivo",
            "journal_id": cash_journal.id,
            "company_id": company.id,
        })
        cls.config = cls.env["pos.config"].sudo().create({
            "name": "VLUX Recibo POS",
            "company_id": company.id,
            "payment_method_ids": [Command.set(payment.ids)],
            "limit_categories": True,
            "iface_available_categ_ids": [Command.set(category.ids)],
        })

    def test_receipt_carries_the_fiscal_identity(self):
        self.start_tour(
            f"/pos/ui?config_id={self.config.id}",
            "VluxReceiptFiscalTour",
            login="admin",
        )
