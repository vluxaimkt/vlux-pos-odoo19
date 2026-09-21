"""VLUX API v1: quick product creation, the same rules as the POS dialog.

``POST /catalog/products`` wraps ``product.template.vlux_pos_quick_create``:
the caller needs the quick-create group, the register must belong to the
caller's company and have an open session (initial stock lands in that
register's location), and a barcode already in use answers ``CONFLICT`` with
the existing product instead of creating a duplicate.
"""
from odoo import http

from odoo.addons.vlux_core.controllers.api import VluxApiError, api_route, int_param, json_body
from odoo.http import request

BODY_FIELDS = (
    "name", "barcode", "list_price", "default_code", "is_storable", "initial_qty",
    "pos_categ_id", "categ_id", "taxes_ids", "image",
)


class VluxApiCatalogWrite(http.Controller):

    @api_route("/catalog/products", scope="catalog:write", methods=("POST",),
               summary="Alta rápida de un producto vendible")
    def create_product(self, token, **kwargs):
        """Create a sellable product from a scanned, unknown barcode.

        Body: ``config_id`` (register) plus ``name``, ``barcode``,
        ``list_price`` and optionally ``default_code``, ``is_storable``,
        ``initial_qty``, ``pos_categ_id``, ``categ_id``, ``taxes_ids`` and
        ``image`` (base64 JPEG/PNG). Answers ``CONFLICT`` with
        ``details.existing`` when the barcode is taken.
        """
        body = json_body()
        config_id = int_param(body.get("config_id"), "config_id", minimum=1)
        if not config_id:
            raise VluxApiError("VALIDATION_ERROR", "Falta config_id (la caja).")
        values = {key: body[key] for key in BODY_FIELDS if key in body}
        result = request.env["product.template"].vlux_pos_quick_create(values, config_id)
        if not result.get("ok"):
            raise VluxApiError(
                "CONFLICT", "El código de barras ya está asignado a otro producto.",
                details={"code": result.get("code"), "existing": result.get("existing")},
            )
        return {
            "product_id": result["product_id"],
            "template_id": result["product_tmpl_id"],
            "barcode": result["barcode"],
        }
