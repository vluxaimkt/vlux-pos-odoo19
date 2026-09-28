from odoo import http
from odoo.http import request


class VluxCreditOwner(http.Controller):
    """Credit views of the VLUX Owner app (only the owner; checked in the model)."""

    @http.route("/vlux_owner/api/credit", type="jsonrpc", auth="user", methods=["POST"], readonly=True)
    def balances(self, **kwargs):
        return request.env["vlux.credit.report"].get_balances()

    @http.route("/vlux_owner/api/credit/statement", type="jsonrpc", auth="user", methods=["POST"], readonly=True)
    def statement(self, partner_id=None, **kwargs):
        return request.env["vlux.credit.report"].get_statement(partner_id)
