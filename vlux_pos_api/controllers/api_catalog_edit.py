"""VLUX API v1: products from the register (quick create and edit).

A person at the register adds a product the store does not know (scanned
barcode) or corrects one (price, description, picture…). Who may do it is the
register's option (``vlux_catalog_editors``: managers and owner by default),
proven by PIN. Both answer the product as the catalog feed sends it, picture
version included, so the register shows the change at once.
"""
from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.vlux_core.controllers.api import VluxApiError, api_route, json_body
from odoo.addons.vlux_core.controllers.api_catalog import _image_versions, _product_payload

from ..models.pos_config import QUICK_CREATE_GROUP
from .api_sales import _acting_employee, _register

EDITABLE = ("name", "list_price", "description", "barcode", "pos_categ_id", "to_weight", "taxes_ids", "image")


def _editor(config, token, body):
    session = config.current_session_id
    if not session or session.state != "opened":
        raise VluxApiError("NO_OPEN_SESSION", "La caja no tiene una sesión abierta.")
    employee, _verified = _acting_employee(config, token, body, required=True)
    if employee:
        allowed = config._vlux_api_can_edit_catalog(employee)
    else:
        # A register without employee login acts as the token's user: Odoo's own rights decide.
        user = request.env.user
        allowed = config._vlux_api_user_in(user, QUICK_CREATE_GROUP) or config._vlux_api_user_in(
            user, "vlux_core.group_vlux_owner")
    if not allowed:
        raise VluxApiError("FORBIDDEN", "No tienes permiso para dar de alta ni editar productos en esta caja.")
    return employee


def _payload(product):
    product.invalidate_recordset()
    return _product_payload(product, _image_versions(request.env, product).get(product.id))


def _refused(error):
    return VluxApiError("VALIDATION_ERROR", str(error.args[0]) if error.args else "Datos inválidos.")


class VluxApiCatalogEdit(http.Controller):

    @api_route("/registers/<int:register_id>/products", scope="orders:write", methods=("POST",),
               summary="Alta rápida de un producto desde la caja (con PIN)")
    def quick_product(self, token, register_id, **kwargs):
        """Body ``{"name", "barcode", "list_price", "taxes_ids"?, "image"?}`` (needs vlux_pos_catalog).

        A taken barcode answers ``CONFLICT``.
        """
        Template = request.env["product.template"]
        if not hasattr(Template, "_vlux_quick_create_for"):
            raise VluxApiError("NOT_FOUND", "El alta rápida no está instalada en esta tienda.")
        config = _register(register_id, token)
        body = json_body()
        _editor(config, token, body)
        values = {key: body[key] for key in ("name", "barcode", "list_price", "taxes_ids", "image") if key in body}
        try:
            result = Template._vlux_quick_create_for(values, config)
        except ValidationError as error:
            raise _refused(error)
        if not result.get("ok"):
            raise VluxApiError("CONFLICT", "El código de barras ya está asignado a otro producto.",
                               details={"existing": result.get("existing")})
        return _payload(request.env["product.product"].sudo().browse(result["product_id"]))

    @api_route("/registers/<int:register_id>/products/<int:product_id>", scope="orders:write", methods=("POST",),
               summary="Editar un producto desde la caja (precio, descripción, foto…; con PIN)")
    def edit_product(self, token, register_id, product_id, **kwargs):
        """Body with any of ``name, list_price, description, barcode, pos_categ_id, to_weight, taxes_ids, image``.

        ``image: null`` removes the picture. ``description`` is plain text.
        """
        config = _register(register_id, token)
        body = json_body()
        actor = _editor(config, token, body)
        product = request.env["product.product"].sudo().with_context(active_test=False).search(
            [("id", "=", product_id), ("company_id", "in", [False, config.company_id.id])], limit=1,
        )
        if not product:
            raise VluxApiError("NOT_FOUND", "El producto no existe.")
        values = {key: body[key] for key in EDITABLE if key in body}
        if not values:
            raise VluxApiError("VALIDATION_ERROR", "No hay cambios.")
        try:
            config._vlux_api_update_product(product, actor, values)
        except (ValidationError, ValueError, TypeError) as error:
            raise _refused(error)
        return _payload(product)
