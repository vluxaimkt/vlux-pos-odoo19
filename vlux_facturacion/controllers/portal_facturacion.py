import base64
import logging

from odoo import _, http
from odoo.http import request
from odoo.addons.point_of_sale.controllers.main import PosController

_logger = logging.getLogger(__name__)


class VluxPosController(PosController):

    @http.route()
    def show_ticket_validation_screen(self, access_token="", **kwargs):
        if not access_token:
            return request.not_found()

        order = request.env["pos.order"].sudo().search(
            [("access_token", "=", access_token)], limit=1
        )
        if not order:
            return request.not_found()

        order = order.with_company(order.company_id).with_context(
            allowed_company_ids=order.company_id.ids
        )
        config = request.env["vlux.fiscal.config"].sudo().search(
            [("company_id", "=", order.company_id.id), ("active", "=", True)],
            limit=1,
        )
        if not config:
            return self._vlux_error(
                _("No existe una configuración fiscal activa para esta empresa.")
            )
        if config.mode != "simulation":
            return self._vlux_error(
                _("El portal V0.3 solamente está habilitado en modo simulación.")
            )

        fiscal_request = request.env["vlux.fiscal.request"].sudo().search(
            [("pos_order_id", "=", order.id)], limit=1
        )
        if fiscal_request.state == "simulation_completed":
            return request.redirect(
                "/vlux/facturacion/result/%s" % fiscal_request.access_token
            )

        values = {
            "fiscal_name": fiscal_request.fiscal_name or "",
            "vat": fiscal_request.vat or "",
            "fiscal_zip": fiscal_request.fiscal_zip or "",
            "fiscal_regime": fiscal_request.fiscal_regime or "",
            "cfdi_use": fiscal_request.cfdi_use or "",
            "email": fiscal_request.email or "",
            "phone": fiscal_request.phone or "",
        }
        errors = {}

        if request.httprequest.method == "POST":
            values = {
                key: (kwargs.get(key) or "").strip()
                for key in values
            }
            if not values["fiscal_name"]:
                errors["fiscal_name"] = _("Captura el nombre o razón social.")
            if not kwargs.get("simulation_ack"):
                errors["simulation_ack"] = _(
                    "Confirma que el documento es una simulación sin validez fiscal."
                )

            if not errors:
                request_values = {
                    "pos_order_id": order.id,
                    "partner_id": order.partner_id.id or False,
                    "account_move_id": order.account_move.id or False,
                    "config_id": config.id,
                    "pac_provider_id": config.pac_provider_id.id,
                    "automatic_processing": config.auto_process,
                    **values,
                }
                try:
                    if fiscal_request:
                        fiscal_request.write(request_values)
                    else:
                        fiscal_request = request.env[
                            "vlux.fiscal.request"
                        ].sudo().create(request_values)
                    fiscal_request.action_process_automatically()
                    return request.redirect(
                        "/vlux/facturacion/result/%s"
                        % fiscal_request.access_token
                    )
                except Exception:
                    _logger.exception(
                        "Error procesando simulación para %s", order.pos_reference
                    )
                    errors["generic"] = _(
                        "No fue posible procesar la solicitud. El incidente quedó registrado."
                    )

        return request.render(
            "vlux_facturacion.portal_facturacion_form",
            {
                "access_token": access_token,
                "pos_order": order,
                "form_values": values,
                "errors": errors,
            },
        )

    def _vlux_error(self, message):
        return request.render(
            "vlux_facturacion.portal_facturacion_result",
            {
                "error_message": message,
                "fiscal_request": False,
                "attachment": False,
            },
        )

    @http.route(
        "/vlux/facturacion/result/<string:token>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
    )
    def vlux_facturacion_result(self, token, **kwargs):
        fiscal_request = request.env["vlux.fiscal.request"].sudo().search(
            [("access_token", "=", token)], limit=1
        )
        if not fiscal_request:
            return request.not_found()
        attachment = request.env["ir.attachment"].sudo().search(
            [
                ("res_model", "=", "vlux.fiscal.request"),
                ("res_id", "=", fiscal_request.id),
                ("name", "ilike", ".xml"),
            ],
            order="id desc",
            limit=1,
        )
        return request.render(
            "vlux_facturacion.portal_facturacion_result",
            {
                "fiscal_request": fiscal_request,
                "attachment": attachment,
                "error_message": False,
            },
        )

    @http.route(
        "/vlux/facturacion/xml/<string:token>",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
    )
    def vlux_facturacion_xml(self, token, **kwargs):
        fiscal_request = request.env["vlux.fiscal.request"].sudo().search(
            [("access_token", "=", token)], limit=1
        )
        if not fiscal_request:
            return request.not_found()
        attachment = request.env["ir.attachment"].sudo().search(
            [
                ("res_model", "=", "vlux.fiscal.request"),
                ("res_id", "=", fiscal_request.id),
                ("name", "ilike", ".xml"),
            ],
            order="id desc",
            limit=1,
        )
        if not attachment or not attachment.datas:
            return request.not_found()
        content = base64.b64decode(attachment.datas)
        return request.make_response(
            content,
            headers=[
                ("Content-Type", "application/xml; charset=utf-8"),
                ("Content-Disposition", 'attachment; filename="%s"' % attachment.name),
                ("Content-Length", str(len(content))),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )
