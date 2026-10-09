"""Serves the VLUX register PWA (built from ``frontend/pos``) at ``/vlux-pos/``.

The page and the worker are public: they hold no data. Everything the
register reads or writes goes through the VLUX API v1 with the device's
bearer token, so an Odoo login is never involved.

The built files have content hashes in their names and are served by Odoo's
static route; only the page, the worker and the manifest need fixed URLs,
which is why they go through this controller (no long cache, and the worker
may control all of ``/vlux-pos/``).
"""
import json

from odoo import http
from odoo.http import Response, request
from odoo.tools import file_open

from odoo.addons.vlux_core.controllers.assets import asset_version

DIST = "vlux_pos_app/static/dist"
SCOPE = "/vlux-pos/"
# Everything the app loads comes from this server: no CDN, no analytics, no
# inline code. Trusted Types forbid feeding strings to DOM sinks such as
# innerHTML or a script URL, so an injected string can never become code; the
# app's single policy ("vlux-pos", in main.tsx) allows only its service worker.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self'; "
    "manifest-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'; "
    "require-trusted-types-for 'script'; trusted-types vlux-pos"
)
# Least privilege: the register uses no browser feature beyond storage yet.
# Hardware (camera scanning, USB printer) opens only what it needs, when it lands.
# Everything off, except the serial port for this page: a scale wired to the
# register's computer is read with Web Serial (the person picks the port).
PERMISSIONS_POLICY = ", ".join([f"{feature}=()" for feature in (
    "accelerometer", "autoplay", "bluetooth", "browsing-topics", "camera", "display-capture",
    "geolocation", "gyroscope", "hid", "idle-detection", "magnetometer", "microphone", "midi",
    "payment", "publickey-credentials-get", "screen-wake-lock", "usb", "xr-spatial-tracking",
)] + ["serial=(self)"])


def _harden(response, html=False):
    """Security headers shared by the page, the worker and the manifest.

    HSTS is left to the TLS front (Caddy, Cloudflare): a local install on a
    self-signed certificate must not be locked out by it.
    """
    headers = response.headers
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "no-referrer"
    headers["Cross-Origin-Resource-Policy"] = "same-origin"
    headers["X-Permitted-Cross-Domain-Policies"] = "none"
    if html:
        csp = CSP
        if request.httprequest.scheme == "https":
            csp += "; upgrade-insecure-requests"
        headers["Content-Security-Policy"] = csp
        headers["Permissions-Policy"] = PERMISSIONS_POLICY
        headers["Cross-Origin-Opener-Policy"] = "same-origin"
        headers["Origin-Agent-Cluster"] = "?1"
        headers["X-Frame-Options"] = "DENY"
    return response


def _dist_file(name):
    """Contents of a built file, or None when the app was not built."""
    try:
        with file_open(f"{DIST}/{name}", "r") as stream:
            return stream.read()
    except FileNotFoundError:
        return None


class VluxPosApp(http.Controller):

    @http.route("/vlux-pos", type="http", auth="public", methods=["GET"], sitemap=False)
    def app_redirect(self, **kwargs):
        return request.redirect(SCOPE, code=301)

    @http.route(SCOPE, type="http", auth="public", methods=["GET"], sitemap=False, save_session=False)
    def app(self, **kwargs):
        html = _dist_file("index.html")
        if html is None:
            return Response("La app del POS no está compilada en este servidor.", status=503,
                            content_type="text/plain; charset=utf-8")
        response = Response(html, content_type="text/html; charset=utf-8")
        # The worker caches this page for offline starts; the browser must
        # still ask the server first so a release is picked up.
        response.headers["Cache-Control"] = "no-cache"
        return _harden(response, html=True)

    @http.route(SCOPE + "sw.js", type="http", auth="public", methods=["GET"], sitemap=False, save_session=False)
    def service_worker(self, **kwargs):
        script = _dist_file("sw.js")
        if script is None:
            return Response(status=404)
        response = Response(script, content_type="application/javascript; charset=utf-8")
        response.headers["Service-Worker-Allowed"] = SCOPE
        response.headers["Cache-Control"] = "no-cache"
        return _harden(response)

    @http.route(SCOPE + "manifest.webmanifest", type="http", auth="public", methods=["GET"], sitemap=False,
                save_session=False)
    def manifest(self, **kwargs):
        version = asset_version("vlux_pos_app")
        payload = {
            "name": "VLUX POS",
            "short_name": "VLUX POS",
            "description": "La caja de tu tienda, con o sin internet.",
            "id": SCOPE,
            "start_url": SCOPE,
            "scope": SCOPE,
            "display": "standalone",
            "orientation": "any",
            "lang": "es-MX",
            "background_color": "#000000",
            "theme_color": "#0d0a14",
            "icons": [
                {"src": f"/vlux_pos_app/static/img/icon-192.png?v={version}", "sizes": "192x192", "type": "image/png"},
                {"src": f"/vlux_pos_app/static/img/icon-512.png?v={version}", "sizes": "512x512", "type": "image/png",
                 "purpose": "any maskable"},
            ],
        }
        response = Response(json.dumps(payload, ensure_ascii=False), content_type="application/manifest+json")
        response.headers["Cache-Control"] = "no-cache"
        return _harden(response)
