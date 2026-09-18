import json
import logging

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import Response, request

_logger = logging.getLogger(__name__)


class VluxCoreHealthController(http.Controller):
    def _json_response(self, payload, status=200):
        response = Response(
            json.dumps(payload, ensure_ascii=False),
            status=status,
            content_type="application/json; charset=utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, max-age=0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @http.route(
        "/vlux/health",
        type="http",
        auth="public",
        methods=["GET"],
        sitemap=False,
        save_session=False,
        readonly=True,
    )
    def health(self, **kwargs):
        return self._json_response({"status": "ok"})

    @http.route(
        "/vlux/ready",
        type="http",
        auth="public",
        methods=["GET"],
        sitemap=False,
        save_session=False,
        readonly=True,
    )
    def ready(self, **kwargs):
        """Readiness: 200 si la instancia puede vender, 503 si no.

        ``/vlux/health`` sigue siendo el liveness que usan Docker, Caddy y el
        service host de Windows; esta ruta es para balanceadores, monitoreo y
        ``vlux-cloud doctor``.
        """
        try:
            payload = request.env["vlux.core.system.info"].sudo().readiness()
        except Exception:
            _logger.exception("VLUX readiness falló")
            payload = {"status": "not_ready", "checks": {"database": "error"}}
        return self._json_response(payload, status=200 if payload["status"] == "ready" else 503)

    @http.route(
        "/vlux/system/info",
        type="http",
        auth="user",
        methods=["GET"],
        sitemap=False,
        readonly=True,
    )
    def system_info(self, **kwargs):
        try:
            info = request.env["vlux.core.system.info"].get_safe_info()
        except AccessError:
            return self._json_response({"ok": False, "error": "forbidden"}, status=403)
        return self._json_response({"ok": True, "data": info})
