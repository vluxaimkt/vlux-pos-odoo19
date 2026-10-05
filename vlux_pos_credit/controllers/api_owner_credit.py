"""The credit section of the owner module in the register: the owner's credit report."""
from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from odoo.addons.vlux_core.controllers.api import VluxApiError, api_route
from odoo.addons.vlux_pos_api.controllers.api_owner import owner_of


class VluxApiOwnerCredit(http.Controller):

    @api_route("/registers/<int:register_id>/owner/credit", scope="orders:write",
               summary="Quién debe, total por cobrar y ventas marcadas (dueño)")
    def balances(self, token, register_id, **kwargs):
        _config, _employee, user = owner_of(register_id, token)
        return request.env["vlux.credit.report"].with_user(user).get_balances()

    @api_route("/registers/<int:register_id>/owner/credit/<int:partner_id>", scope="orders:write",
               summary="Estado de cuenta de un cliente (dueño)")
    def statement(self, token, register_id, partner_id, **kwargs):
        _config, _employee, user = owner_of(register_id, token)
        try:
            return request.env["vlux.credit.report"].with_user(user).get_statement(partner_id)
        except AccessError as error:
            raise VluxApiError("NOT_FOUND", str(error.args[0]) if error.args else "No existe.")
