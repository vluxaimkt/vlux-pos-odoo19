from odoo.addons.point_of_sale.models.product_template import ProductTemplate as PosProductTemplate
from odoo.fields import Command
from odoo.tests import tagged

from .common import VluxCatalogCase


@tagged("post_install", "-at_install")
class TestVluxPosLoad(VluxCatalogCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Product = cls.env["product.template"].sudo()
        cls.plain = Product.create({"name": "VLUX Load simple", "available_in_pos": True})
        color = cls.env["product.attribute"].sudo().create({
            "name": "VLUX Load color",
            "value_ids": [Command.create({"name": name}) for name in ("Rojo", "Azul", "Verde")],
        })
        cls.variant_template = Product.create({
            "name": "VLUX Load variantes",
            "available_in_pos": True,
            "attribute_line_ids": [Command.create({
                "attribute_id": color.id,
                "value_ids": [Command.set(color.value_ids.ids)],
            })],
        })
        # write() en vez de action_archive(): la clase base deja sesiones POS abiertas.
        cls.variant_template.product_variant_ids[:1].write({"active": False})

    def _standard(self, templates):
        """Reference: the unmodified point_of_sale implementation."""
        rows = [{"id": template.id} for template in templates]
        PosProductTemplate._add_archived_combinations(self.env["product.template"], rows)
        return {row["id"]: row["_archived_combinations"] for row in rows}

    def _optimized(self, templates):
        rows = [{"id": template.id} for template in templates]
        self.env["product.template"]._add_archived_combinations(rows)
        return {row["id"]: row["_archived_combinations"] for row in rows}

    def test_archived_combinations_match_standard(self):
        templates = self.plain | self.variant_template
        expected = self._standard(templates)

        self.assertEqual(self._optimized(templates), expected)
        self.assertEqual(expected[self.plain.id], [])
        self.assertTrue(expected[self.variant_template.id], "the archived variant must be reported")

    def test_plain_templates_skip_exclusion_computation(self):
        templates = self.env["product.template"].sudo().create([
            {"name": f"VLUX Load masivo {index}", "available_in_pos": True} for index in range(30)
        ])
        self.env.invalidate_all()
        before = self.env.cr.sql_log_count
        result = self._optimized(templates)
        queries = self.env.cr.sql_log_count - before

        self.assertEqual(len(result), 30)
        self.assertTrue(all(value == [] for value in result.values()))
        self.assertLessEqual(queries, 2)

    def test_pos_session_payload_keeps_archived_combinations(self):
        data = self.session_a.load_data([])
        by_id = {row["id"]: row for row in data["product.template"]}

        self.assertEqual(by_id[self.plain.id]["_archived_combinations"], [])
        self.assertEqual(
            by_id[self.variant_template.id]["_archived_combinations"],
            self._standard(self.variant_template)[self.variant_template.id],
        )
