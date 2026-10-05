"""VLUX API v1: the owner module of the register (owner only, by PIN).

The same figures as VLUX Owner, computed by the same server code (here run
as the owner's own user), plus the register's options and the log of
authorizations. Which sections exist depends on the installed modules
(``pos.config._vlux_api_owner_sections``).
"""
from odoo import fields, http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request

from odoo.addons.vlux_core.controllers.api import VluxApiError, api_route, int_param, json_body

from .api_sales import _acting_employee, _register


def owner_of(register_id, token, body=None):
    """``(config, owner employee, owner user)``, or FORBIDDEN / PIN_REQUIRED."""
    config = _register(register_id, token)
    if not config.module_pos_hr:
        raise VluxApiError("VALIDATION_ERROR", "Esta caja no usa empleados.")
    employee, _verified = _acting_employee(config, token, body or {}, required=True)
    if not config._vlux_api_is_owner(employee):
        raise VluxApiError("FORBIDDEN", "Sólo el dueño entra a este módulo.")
    user = employee.sudo().user_id
    if not user:
        raise VluxApiError("FORBIDDEN", "El dueño necesita su usuario de Odoo ligado al empleado.")
    return config, employee, user


class VluxApiOwner(http.Controller):

    @api_route("/registers/<int:register_id>/owner/sections", scope="orders:write",
               summary="Secciones del módulo del dueño en esta tienda")
    def sections(self, token, register_id, **kwargs):
        config, _employee, _user = owner_of(register_id, token)
        return {"items": config._vlux_api_owner_sections()}

    @api_route("/registers/<int:register_id>/owner/dashboard", scope="orders:write",
               summary="Resumen del día (el mismo de VLUX Owner)",
               params=[{"name": "date", "in": "query", "schema": {"type": "string", "format": "date"}}])
    def dashboard(self, token, register_id, date=None, **kwargs):
        _config, _employee, user = owner_of(register_id, token)
        if "vlux.owner.dashboard.service" not in request.env:
            raise VluxApiError("NOT_FOUND", "VLUX Owner no está instalado en esta tienda.")
        try:
            return request.env["vlux.owner.dashboard.service"].with_user(user).get_dashboard(date=date or None)
        except (AccessError, ValidationError, ValueError) as error:
            raise VluxApiError("VALIDATION_ERROR", str(error.args[0]) if error.args else "No disponible.")

    @api_route("/registers/<int:register_id>/owner/options", scope="orders:write",
               summary="Opciones de la caja que el dueño puede cambiar")
    def options(self, token, register_id, **kwargs):
        config, _employee, _user = owner_of(register_id, token)
        return {"items": config._vlux_api_options_form()}

    @api_route("/registers/<int:register_id>/owner/options/save", scope="orders:write", methods=("POST",),
               summary="Cambiar opciones de la caja")
    def save_options(self, token, register_id, **kwargs):
        """Body ``{"values": {field: value}}`` with only published options."""
        body = json_body()
        config, employee, _user = owner_of(register_id, token, body)
        values = body.get("values")
        if not isinstance(values, dict) or not values:
            raise VluxApiError("VALIDATION_ERROR", "values debe ser un objeto con las opciones a cambiar.")
        try:
            return {"items": config._vlux_api_set_options(employee, values)}
        except ValidationError as error:
            raise VluxApiError("VALIDATION_ERROR", str(error.args[0]) if error.args else "Datos inválidos.")

    @api_route("/registers/<int:register_id>/owner/authorizations", scope="orders:write",
               summary="Quién autorizó qué en esta caja",
               params=[{"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50}}])
    def authorizations(self, token, register_id, limit=None, **kwargs):
        config, _employee, _user = owner_of(register_id, token)
        rows = request.env["vlux.pos.authorization"].sudo().search(
            [("pos_config_id", "=", config.id)], limit=int_param(limit, "limit", 50, 1, 200),
        )
        return {"items": [{
            "date": fields.Datetime.to_string(row.create_date) + "Z",
            "authorized_by": row.employee_id.name,
            "requested_by": row.requested_by_id.name or None,
            "purpose": row.purpose,
        } for row in rows]}
