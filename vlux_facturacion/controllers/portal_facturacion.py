import base64
import logging
from datetime import timedelta

from odoo import _, fields, http
from odoo.addons.point_of_sale.controllers.main import PosController
from odoo.http import content_disposition, request

_logger = logging.getLogger(__name__)

FISCAL_COOKIE = "vlux_fiscal_session"
FISCAL_COOKIE_MAX_AGE = 60 * 60


class VluxPosController(PosController):

    def _client_identity(self):
        return request.httprequest.remote_addr or "unknown"

    def _consume_rate_limit(self, scope, limit, window_seconds, identity=None):
        return request.env["vlux.fiscal.rate.limit"].sudo().consume(
            scope,
            identity or self._client_identity(),
            limit,
            window_seconds,
        )

    def _secure_response(self, response):
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    def _rate_limit_response(self):
        response = request.make_response(
            _("Demasiadas solicitudes. Intente nuevamente más tarde."),
            status=429,
        )
        response.headers["Retry-After"] = "60"
        return self._secure_response(response)

    def _not_found_response(self):
        response = request.make_response(
            _("El recurso solicitado no existe o ya no está disponible."),
            status=404,
        )
        return self._secure_response(response)

    def _redirect_to_public_access(self, fiscal_request):
        raw_token = fiscal_request.issue_public_link_token()
        response = request.redirect(
            f"/vlux/facturacion/access/{raw_token}",
            code=303,
        )
        return self._secure_response(response)

    def _set_fiscal_cookie(self, response, raw_token):
        response.set_cookie(
            FISCAL_COOKIE,
            raw_token,
            max_age=FISCAL_COOKIE_MAX_AGE,
            expires=fields.Datetime.now() + timedelta(seconds=FISCAL_COOKIE_MAX_AGE),
            path="/vlux/facturacion/",
            secure=request.httprequest.scheme == "https",
            httponly=True,
            samesite="Lax",
            cookie_type="required",
        )
        return response

    def _exchange_token_response(self, token):
        if not self._consume_rate_limit("token_exchange", 10, 60):
            return self._rate_limit_response()
        fiscal_request, session_token = request.env[
            "vlux.fiscal.request"
        ].exchange_public_link_token(token)
        if not fiscal_request:
            return self._not_found_response()
        location = f"/vlux/facturacion/request/{fiscal_request.id}"
        response = request.redirect(location, code=303)
        self._set_fiscal_cookie(response, session_token)
        return self._secure_response(response)

    def _authenticated_fiscal_request(self, request_id):
        raw_token = request.httprequest.cookies.get(FISCAL_COOKIE, "")
        return request.env["vlux.fiscal.request"].authenticate_public_token(
            raw_token,
            request_id=request_id,
        )

    @http.route()
    def show_ticket_validation_screen(self, access_token="", **kwargs):
        if not self._consume_rate_limit("ticket_validation", 30, 60):
            return self._rate_limit_response()
        if not access_token:
            return self._not_found_response()

        order = request.env["pos.order"].sudo().search(
            [("access_token", "=", access_token)], limit=1
        )
        if not order:
            return self._not_found_response()

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
                _("El portal solamente está habilitado en modo simulación.")
            )

        fiscal_request = request.env["vlux.fiscal.request"].sudo().search(
            [("pos_order_id", "=", order.id)], limit=1
        )
        if fiscal_request.state == "simulation_completed":
            return self._redirect_to_public_access(fiscal_request)

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
                    with request.env.cr.savepoint():
                        if fiscal_request:
                            fiscal_request.write(request_values)
                        else:
                            fiscal_request = request.env[
                                "vlux.fiscal.request"
                            ].sudo().create(request_values)
                        fiscal_request.action_process_automatically()
                    return self._redirect_to_public_access(fiscal_request)
                except Exception:
                    _logger.exception(
                        "Error procesando simulación para %s",
                        order.pos_reference,
                    )
                    errors["generic"] = _(
                        "No fue posible procesar la solicitud. "
                        "El incidente quedó registrado."
                    )

        response = request.render(
            "vlux_facturacion.portal_facturacion_form",
            {
                "access_token": access_token,
                "pos_order": order,
                "form_values": values,
                "errors": errors,
            },
        )
        return self._secure_response(response)

    def _vlux_error(self, message):
        response = request.render(
            "vlux_facturacion.portal_facturacion_result",
            {
                "error_message": message,
                "fiscal_request": False,
                "attachment": False,
            },
        )
        return self._secure_response(response)

    @http.route(
        "/vlux/facturacion/access/<string:token>",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
        save_session=False,
    )
    def vlux_facturacion_access(self, token, **kwargs):
        return self._exchange_token_response(token)

    @http.route(
        "/vlux/facturacion/request/<int:request_id>",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
        save_session=False,
    )
    def vlux_facturacion_result(self, request_id, **kwargs):
        if not self._consume_rate_limit("result", 60, 60):
            return self._rate_limit_response()
        fiscal_request = self._authenticated_fiscal_request(request_id)
        if not fiscal_request:
            return self._not_found_response()
        attachment = request.env["ir.attachment"].sudo().search(
            [
                ("res_model", "=", "vlux.fiscal.request"),
                ("res_id", "=", fiscal_request.id),
                ("name", "ilike", ".xml"),
            ],
            order="id desc",
            limit=1,
        )
        response = request.render(
            "vlux_facturacion.portal_facturacion_result",
            {
                "fiscal_request": fiscal_request,
                "attachment": attachment,
                "error_message": False,
            },
        )
        return self._secure_response(response)

    @http.route(
        "/vlux/facturacion/document/<int:request_id>/xml",
        type="http",
        auth="public",
        methods=["GET"],
        website=True,
        sitemap=False,
        save_session=False,
    )
    def vlux_facturacion_xml(self, request_id, **kwargs):
        if not self._consume_rate_limit("xml_download", 30, 60):
            return self._rate_limit_response()
        fiscal_request = self._authenticated_fiscal_request(request_id)
        if not fiscal_request:
            return self._not_found_response()
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
            return self._not_found_response()
        content = base64.b64decode(attachment.datas)
        response = request.make_response(
            content,
            headers=[
                ("Content-Type", "application/xml; charset=utf-8"),
                ("Content-Disposition", content_disposition(attachment.name)),
                ("Content-Length", str(len(content))),
            ],
        )
        return self._secure_response(response)
