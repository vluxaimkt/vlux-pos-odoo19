import json
import logging

from odoo import http
from odoo.addons.vlux_core.controllers.assets import asset_version
from odoo.exceptions import AccessError
from odoo.http import Response, request
from odoo.tools import file_open

_logger = logging.getLogger(__name__)


class VluxOwnerController(http.Controller):

    def _ensure_owner_access(self):
        if not (
            request.env.user.has_group("vlux_owner.group_vlux_owner")
            or request.env.user.has_group("vlux_core.group_vlux_owner")
        ):
            raise AccessError("Tu usuario no tiene acceso a VLUX Owner.")

    def _json_response(self, payload, status=200):
        response = Response(
            json.dumps(payload, ensure_ascii=False),
            status=status,
            content_type="application/json; charset=utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def _secure_html_response(self, response):
        response.headers["Cache-Control"] = "no-store, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; worker-src 'self'; "
            "manifest-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        return response

    @http.route("/vlux-owner", type="http", auth="user", methods=["GET"], sitemap=False)
    def owner_redirect(self, **kwargs):
        return request.redirect("/vlux-owner/")

    @http.route("/vlux-owner/", type="http", auth="user", methods=["GET"], sitemap=False)
    def owner_app(self, **kwargs):
        self._ensure_owner_access()
        return self._secure_html_response(
            request.render("vlux_owner.owner_app", {"asset_version": asset_version("vlux_owner")})
        )

    @http.route("/vlux_owner/api/dashboard", type="jsonrpc", auth="user", methods=["POST"], readonly=True)
    def dashboard(self, date=None, **kwargs):
        self._ensure_owner_access()
        return request.env["vlux.owner.dashboard.service"].get_dashboard(date=date)

    @http.route(
        "/vlux_owner/api/share/dashboard",
        type="http",
        auth="bearer",
        methods=["POST"],
        csrf=False,
        save_session=False,
        sitemap=False,
    )
    def shared_dashboard(self, **kwargs):
        authorization = request.httprequest.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            return self._json_response({"ok": False, "error": "unauthorized"}, status=401)
        if not (
            request.env.user.has_group("vlux_owner.group_vlux_owner")
            or request.env.user.has_group("vlux_core.group_vlux_owner")
        ):
            return self._json_response({"ok": False, "error": "forbidden"}, status=403)
        if not request.httprequest.is_json:
            return self._json_response(
                {"ok": False, "error": "invalid_content_type"},
                status=415,
            )
        if (request.httprequest.content_length or 0) > 16 * 1024:
            return self._json_response(
                {"ok": False, "error": "request_too_large"},
                status=413,
            )
        allowed = request.env["vlux.rate.limit"].sudo().consume(
            "shared_dashboard",
            request.env.user.id,
            30,
            60,
        )
        if not allowed:
            response = self._json_response(
                {"ok": False, "error": "rate_limited"},
                status=429,
            )
            response.headers["Retry-After"] = "60"
            return response

        try:
            payload = request.httprequest.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                return self._json_response(
                    {"ok": False, "error": "invalid_request"},
                    status=400,
                )
            requested_date = payload.get("date")
            with request.env.cr.savepoint():
                service = request.env[
                    "vlux.owner.dashboard.service"
                ].with_company(request.env.company)
                data = service.get_dashboard(date=requested_date)
            return self._json_response({"ok": True, "data": data})
        except (TypeError, ValueError):
            return self._json_response({"ok": False, "error": "invalid_request"}, status=400)
        except Exception:
            _logger.exception("No fue posible generar el dashboard compartido.")
            return self._json_response({"ok": False, "error": "dashboard_unavailable"}, status=503)

    @http.route("/vlux-owner/manifest.webmanifest", type="http", auth="public", methods=["GET"], sitemap=False)
    def manifest(self, **kwargs):
        version = asset_version("vlux_owner")
        payload = {
            "name": "VLUX Owner",
            "short_name": "VLUX Owner",
            "description": "Tu negocio en la palma de tu mano.",
            "start_url": "/vlux-owner/",
            "scope": "/vlux-owner/",
            "display": "standalone",
            "background_color": "#09080e",
            "theme_color": "#0d0a14",
            "icons": [
                {"src": f"/vlux_owner/static/img/icon-192.png?v={version}", "sizes": "192x192", "type": "image/png"},
                {"src": f"/vlux_owner/static/img/icon-512.png?v={version}", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            ],
        }
        return Response(json.dumps(payload), content_type="application/manifest+json")

    @http.route("/vlux-owner/sw.js", type="http", auth="public", methods=["GET"], sitemap=False)
    def service_worker(self, **kwargs):
        with file_open("vlux_owner/static/src/sw.js", "r") as stream:
            content = stream.read().replace("__VLUX_ASSET_VERSION__", asset_version("vlux_owner"))
        response = Response(content, content_type="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/vlux-owner/"
        response.headers["Cache-Control"] = "no-cache"
        return response
