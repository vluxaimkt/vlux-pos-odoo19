from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.model
    def _vlux_templates_with_attribute_values(self, template_ids):
        """Ids of the templates that own at least one attribute value (active or not)."""
        if not template_ids:
            return set()
        rows = self.env["product.template.attribute.value"].sudo()._read_group(
            [("product_tmpl_id", "in", list(template_ids))], ["product_tmpl_id"]
        )
        return {template.id for [template] in rows}

    def _add_archived_combinations(self, products):
        """Skip the per-template exclusion computation for templates without attributes.

        The standard implementation calls ``_get_attribute_exclusions`` once per
        loaded template (several queries and computed fields each). A template
        without any ``product.template.attribute.value`` can have neither
        exclusions nor archived combinations, so its result is always ``[]``.
        Retail catalogs are mostly such products: this removes the dominant cost
        of ``product.template`` loading without changing the payload.
        """
        with_attributes = self._vlux_templates_with_attribute_values(
            [product["id"] for product in products]
        )
        remaining = []
        for product in products:
            if product["id"] in with_attributes:
                remaining.append(product)
            else:
                product["_archived_combinations"] = []
        if remaining:
            super()._add_archived_combinations(remaining)
