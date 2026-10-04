"""VLUX API v1: the register's employees module (name, access to the register, PIN).

Only whoever the register's option allows (the owner by default, see
``pos.config.vlux_staff_admins``), proven by PIN. Access is pos_hr's own:
the register's manager, cashier and minimal-access lists, so the Odoo POS and
the VLUX register see the same people with the same rights. Nobody is
deleted: an employee who leaves is archived.
"""
from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.vlux_core.controllers.api import VluxApiError, api_route, json_body

from .api_sales import _acting_employee, _register


def _manager_of_staff(register_id, token, body=None):
    config = _register(register_id, token)
    if not config.module_pos_hr:
        raise VluxApiError("VALIDATION_ERROR", "Esta caja no usa empleados.")
    employee, _verified = _acting_employee(config, token, body or {}, required=True)
    if not config._vlux_api_can_manage_staff(employee):
        raise VluxApiError("FORBIDDEN", "No tienes permiso para administrar empleados en esta caja.")
    return config, employee


def _refused(error):
    return VluxApiError("VALIDATION_ERROR", str(error.args[0]) if error.args else "Datos inválidos.")


class VluxApiStaff(http.Controller):

    @api_route("/registers/<int:register_id>/staff", scope="session:manage",
               summary="Empleados de la tienda y su acceso a esta caja (dueño)")
    def staff(self, token, register_id, **kwargs):
        config, _employee = _manager_of_staff(register_id, token)
        return {"items": config._vlux_api_staff()}

    @api_route("/registers/<int:register_id>/staff/new", scope="session:manage", methods=("POST",),
               summary="Dar de alta un empleado con su acceso y PIN")
    def add(self, token, register_id, **kwargs):
        """Body ``{"name", "role": "manager"|"cashier"|"minimal", "pin"}``."""
        body = json_body()
        config, actor = _manager_of_staff(register_id, token, body)
        try:
            employee = config._vlux_api_add_staff(actor, body.get("name"), body.get("role"), body.get("pin"))
        except ValidationError as error:
            raise _refused(error)
        return next(row for row in config._vlux_api_staff() if row["id"] == employee.id)

    @api_route("/registers/<int:register_id>/staff/<int:employee_id>", scope="session:manage", methods=("POST",),
               summary="Cambiar nombre, acceso, PIN o dar de baja a un empleado")
    def change(self, token, register_id, employee_id, **kwargs):
        """Body with any of ``{"name", "role", "pin", "active"}``; ``"role": "none"`` takes away access here."""
        body = json_body()
        config, actor = _manager_of_staff(register_id, token, body)
        employee = request.env["hr.employee"].sudo().with_context(active_test=False).search(
            [("id", "=", employee_id), ("company_id", "in", [False, config.company_id.id])], limit=1,
        )
        if not employee:
            raise VluxApiError("NOT_FOUND", "El empleado no existe.")
        values = {key: body[key] for key in ("name", "role", "pin", "active") if key in body}
        if "active" in values and not isinstance(values["active"], bool):
            raise VluxApiError("VALIDATION_ERROR", "active debe ser verdadero o falso.")
        try:
            config._vlux_api_set_staff(employee, actor, **values)
        except ValidationError as error:
            raise _refused(error)
        return next(row for row in config._vlux_api_staff() if row["id"] == employee.id)
