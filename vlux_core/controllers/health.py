import json

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import Response, request


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
