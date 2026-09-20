from odoo.modules.module import get_manifest
from odoo.tests.common import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestVluxOwnerAssets(HttpCase):
    """Same contract as the scanner page: every release gets its own asset URL."""

    def test_owner_page_assets_carry_the_addon_version(self):
        version = get_manifest("vlux_owner")["version"]
        # The page is Owner-only; give the test user the role it checks for.
        self.env.ref("base.user_admin").sudo().write(
            {"group_ids": [(4, self.env.ref("vlux_core.group_vlux_owner").id)]}
        )
        self.env.flush_all()
        self.authenticate("admin", "admin")
        body = self.url_open("/vlux-owner/").text

        for asset in ("app.css", "app.js"):
            url = f"/vlux_owner/static/dist/assets/{asset}?v={version}"
            self.assertIn(url, body, f"{asset} is served without the version query")
        self.assertNotIn(
            '"/vlux_owner/static/dist/assets/app.js"',
            body,
            "an unversioned app.js would be cached across releases",
        )

    def test_service_worker_and_manifest_are_versioned(self):
        version = get_manifest("vlux_owner")["version"]

        worker = self.url_open("/vlux-owner/sw.js")
        self.assertEqual(worker.status_code, 200)
        self.assertNotIn("__VLUX_ASSET_VERSION__", worker.text, "the placeholder was not replaced")
        self.assertIn(f'const ASSET_VERSION = "{version}"', worker.text)
        self.assertIn(f"/vlux_owner/static/dist/assets/app.js?v=", worker.text)
        self.assertNotIn('"/vlux_owner/static/dist/assets/app.js",', worker.text)

        manifest = self.url_open("/vlux-owner/manifest.webmanifest").json()
        for icon in manifest["icons"]:
            self.assertIn(f"?v={version}", icon["src"])
