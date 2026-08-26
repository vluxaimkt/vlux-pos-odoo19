import hmac
import json

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import Response, request
from odoo.tools import file_open


class VluxOwnerController(http.Controller):

    def _ensure_owner_access(self):
        if not request.env.user.has_group("vlux_owner.group_vlux_owner"):
            raise AccessError("Tu usuario no tiene acceso a VLUX Owner.")

    def _json_response(self, payload, status=200):
        response = Response(
            json.dumps(payload, ensure_ascii=False),
            status=status,
            content_type="application/json; charset=utf-8",
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    @http.route("/vlux-owner", type="http", auth="user", methods=["GET"], sitemap=False)
    def owner_redirect(self, **kwargs):
        return request.redirect("/vlux-owner/")

    @http.route("/vlux-owner/", type="http", auth="user", methods=["GET"], sitemap=False)
    def owner_app(self, **kwargs):
        self._ensure_owner_access()
        return request.render("vlux_owner.owner_app")

    @http.route("/vlux_owner/api/dashboard", type="jsonrpc", auth="user", methods=["POST"], readonly=True)
    def dashboard(self, date=None, **kwargs):
        self._ensure_owner_access()
        return request.env["vlux.owner.dashboard.service"].get_dashboard(date=date)

    @http.route(
        "/vlux_owner/api/share/dashboard",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
        sitemap=False,
    )
    def shared_dashboard(self, **kwargs):
        params = request.env["ir.config_parameter"].sudo()
        expected_token = params.get_param("vlux_owner.demo_api_token", "")
        provided_token = request.httprequest.headers.get("X-VLUX-Owner-Token", "")

        if not expected_token or not provided_token or not hmac.compare_digest(expected_token, provided_token):
            return self._json_response({"ok": False, "error": "unauthorized"}, status=401)

        try:
            payload = request.httprequest.get_json(silent=True) or {}
            requested_date = payload.get("date")

            user_id = int(params.get_param("vlux_owner.demo_user_id", "2") or 2)
            demo_user = request.env["res.users"].sudo().browse(user_id).exists()
            if not demo_user or not demo_user.active:
                return self._json_response({"ok": False, "error": "demo_user_unavailable"}, status=503)

            service = (
                request.env["vlux.owner.dashboard.service"]
                .with_user(demo_user)
                .with_company(demo_user.company_id)
            )
            data = service.get_dashboard(date=requested_date)
            return self._json_response({"ok": True, "data": data})
        except (TypeError, ValueError):
            return self._json_response({"ok": False, "error": "invalid_request"}, status=400)
        except Exception:
            request.env.cr.rollback()
            return self._json_response({"ok": False, "error": "dashboard_unavailable"}, status=503)

    @http.route("/vlux-owner/manifest.webmanifest", type="http", auth="public", methods=["GET"], sitemap=False)
    def manifest(self, **kwargs):
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
                {"src": "/vlux_owner/static/img/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/vlux_owner/static/img/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            ],
        }
        return Response(json.dumps(payload), content_type="application/manifest+json")

    @http.route("/vlux-owner/sw.js", type="http", auth="public", methods=["GET"], sitemap=False)
    def service_worker(self, **kwargs):
        with file_open("vlux_owner/static/src/sw.js", "rb") as stream:
            content = stream.read()
        response = Response(content, content_type="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/vlux-owner/"
        response.headers["Cache-Control"] = "no-cache"
        return response
