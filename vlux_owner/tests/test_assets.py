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
