import json

from odoo import http
from odoo.http import request


class BraillePlatformManifest(http.Controller):

    @http.route(
        "/braille/manifest.webmanifest",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def braille_manifest(self, **kwargs):
        database = request.db or "braille_dev"
        manifest = {
            "name": "Braille International",
            "short_name": "Braille",
            "description": "Plataforma empresarial Braille International",
            "lang": "es-MX",
            "start_url": f"/web?db={database}",
            "scope": "/",
            "display": "standalone",
            "display_override": ["window-controls-overlay", "standalone"],
            "background_color": "#141516",
            "theme_color": "#E01F25",
            "icons": [
                {
                    "src": "/braille_brand_theme/static/src/img/braille_icon_192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": "/braille_brand_theme/static/src/img/braille_icon_512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        }
        return request.make_response(
            json.dumps(manifest, ensure_ascii=False),
            headers=[
                ("Content-Type", "application/manifest+json; charset=utf-8"),
                ("Cache-Control", "public, max-age=3600"),
            ],
        )
