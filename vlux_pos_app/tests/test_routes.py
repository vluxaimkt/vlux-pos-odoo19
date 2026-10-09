"""The PWA is served whole: page, worker with its scope, manifest and built files."""
import json
import re

from odoo.tests import HttpCase, tagged
from odoo.tools import file_path


@tagged("post_install", "-at_install")
class TestVluxPosAppRoutes(HttpCase):

    def test_page_is_served_with_a_strict_policy(self):
        response = self.url_open("/vlux-pos/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('<div id="app">', response.text)
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertIn("connect-src 'self'", policy, "the token may only ever be sent to this server")
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("require-trusted-types-for 'script'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertNotIn("*", policy)
        headers = response.headers
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(headers["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        for feature in ("camera", "microphone", "geolocation", "payment", "usb"):
            self.assertIn(f"{feature}=()", headers["Permissions-Policy"])
        # Only the register itself may use the serial port (a scale wired to the computer).
        self.assertIn("serial=(self)", headers["Permissions-Policy"])
        self.assertNotIn("serial=*", headers["Permissions-Policy"])
        # The page itself carries no configuration, token or server detail.
        self.assertNotIn("token", response.text.lower())

    def test_worker_and_manifest_are_not_sniffable_or_embeddable_elsewhere(self):
        for path in ("/vlux-pos/sw.js", "/vlux-pos/manifest.webmanifest"):
            headers = self.url_open(path).headers
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff", path)
            self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin", path)

    def test_the_built_app_holds_no_secret_or_server_address(self):
        # Built from source in CI: nothing about a particular store may be in it.
        html = self.url_open("/vlux-pos/").text
        for asset in re.findall(r'(?:src|href)="(/vlux_pos_app/static/dist/[^"]+)"', html):
            body = self.url_open(asset).text
            for needle in ("vlux.com.mx", "ts.net", "127.0.0.1", "Bearer ey", "password="):
                self.assertNotIn(needle, body, f"{needle} in {asset}")

    def test_every_built_file_the_page_references_exists(self):
        html = self.url_open("/vlux-pos/").text
        assets = re.findall(r'(?:src|href)="(/vlux_pos_app/static/dist/[^"]+)"', html)
        self.assertTrue(any(asset.endswith(".js") for asset in assets), assets)
        self.assertTrue(any(asset.endswith(".css") for asset in assets), assets)
        for asset in assets:
            self.assertEqual(self.url_open(asset).status_code, 200, asset)
            file_path(asset.lstrip("/"))  # raises if it is not in the addon

    def test_worker_may_control_the_whole_app(self):
        response = self.url_open("/vlux-pos/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/vlux-pos/")
        self.assertIn("javascript", response.headers["Content-Type"])
        # The precache list was injected at build time.
        self.assertNotIn("self.__WB_MANIFEST", response.text)
        self.assertIn("/vlux_pos_app/static/dist/assets/", response.text)

    def test_manifest_makes_it_installable(self):
        response = self.url_open("/vlux-pos/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        manifest = json.loads(response.text)
        self.assertEqual(manifest["start_url"], "/vlux-pos/")
        self.assertEqual(manifest["scope"], "/vlux-pos/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual({icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"})
        for icon in manifest["icons"]:
            self.assertEqual(self.url_open(icon["src"]).status_code, 200)

    def test_bare_path_redirects_into_the_scope(self):
        response = self.url_open("/vlux-pos", allow_redirects=False)
        self.assertIn(response.status_code, (301, 308))
        self.assertTrue(response.headers["Location"].endswith("/vlux-pos/"))
