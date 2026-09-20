"""Cache busting for files served straight from ``/<addon>/static/...``.

Odoo serves those files with ``Cache-Control: public, max-age=604800``, so a
CDN (the Cloudflare tunnel in front of a cloud tenant) and the phones keep the
previous copy for up to a week after an upgrade. Odoo's own asset bundles carry
a content hash in the URL and are therefore safe; our standalone pages (the
mobile scanner and the Owner PWA) reference their files by a fixed path.

Appending the addon's version to the query string gives each release its own
URL, so an upgrade invalidates both the CDN entry and the browser cache without
anyone purging anything.
"""
from odoo.modules.module import get_manifest


def asset_version(addon: str) -> str:
    """Version of ``addon`` for use as a ``?v=`` cache buster."""
    return str(get_manifest(addon).get("version") or "0")
