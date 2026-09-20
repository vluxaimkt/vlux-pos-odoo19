from odoo.modules.module import get_manifest
from odoo.tests.common import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestVluxScannerAssets(HttpCase):
    """The phone page must not be served with last release's JavaScript.

    Odoo serves /<addon>/static/... with a one-week Cache-Control, so a CDN in
    front of a tenant (the Cloudflare tunnel) keeps the previous file after an
    upgrade. Every release must therefore produce a different URL.
    """

    def test_scanner_page_assets_carry_the_addon_version(self):
        version = get_manifest("vlux_mobile_scanner")["version"]
        body = self.url_open("/vlux/scanner").text

        for asset in ("scanner.css", "scanner_core.js", "scanner.js"):
            url = f"/vlux_mobile_scanner/static/src/scanner/{asset}?v={version}"
            self.assertIn(url, body, f"{asset} is served without the version query")
        self.assertNotIn(
            '"/vlux_mobile_scanner/static/src/scanner/scanner.js"',
            body,
            "an unversioned scanner.js would be cached across releases",
        )

    def test_versioned_asset_is_served(self):
        version = get_manifest("vlux_mobile_scanner")["version"]
        response = self.url_open(
            f"/vlux_mobile_scanner/static/src/scanner/scanner.js?v={version}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("repeatButton", response.text)
