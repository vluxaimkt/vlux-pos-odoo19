import json
from unittest.mock import patch

from odoo.tests.common import HttpCase, tagged

from odoo.addons.vlux_core.models import system_info

ALLOWED_VALUES = {"ok", "error", "missing", "pending"}


@tagged("post_install", "-at_install")
class TestVluxReady(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not cls.env["pos.config"].search_count([("active", "=", True)]):
            cls.env["pos.config"].create({"name": "VLUX Ready Caja"})

    def _get_ready(self):
        response = self.url_open("/vlux/ready")
        return response.status_code, json.loads(response.text), response

    def _assert_safe(self, payload):
        self.assertEqual(set(payload), {"status", "checks"})
        self.assertIn(payload["status"], {"ready", "not_ready"})
        self.assertTrue(set(payload["checks"].values()) <= ALLOWED_VALUES, payload)
        text = json.dumps(payload).lower()
        for secret in ("password", "token", "vlux_", self.env.cr.dbname.lower(), "\\\\", "/"):
            self.assertNotIn(secret, text)

    def test_ready_when_everything_is_in_place(self):
        status, payload, response = self._get_ready()

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload, {
            "status": "ready",
            "checks": {"database": "ok", "addons": "ok", "module_updates": "ok", "pos_config": "ok"},
        })
        self.assertIn("no-store", response.headers["Cache-Control"])
        self._assert_safe(payload)

    def test_not_ready_when_a_required_addon_is_missing(self):
        required = dict(system_info.EDITION_REQUIRED_ADDONS)
        required["local_core"] = ("vlux_core", "vlux_addon_que_no_existe")
        self.env["ir.config_parameter"].sudo().set_param("vlux_core.edition", "local_core")
        with patch.object(system_info, "EDITION_REQUIRED_ADDONS", required):
            status, payload, _response = self._get_ready()

        self.assertEqual(status, 503)
        self.assertEqual(payload["status"], "not_ready")
        self.assertEqual(payload["checks"]["addons"], "missing")
        self._assert_safe(payload)

    def test_not_ready_while_a_module_update_is_pending(self):
        self.env.ref("base.module_vlux_core").sudo().write({"state": "to upgrade"})
        status, payload, _response = self._get_ready()

        self.assertEqual(status, 503)
        self.assertEqual(payload["checks"]["module_updates"], "pending")

    def test_not_ready_without_an_active_register(self):
        # SQL: el ORM prohíbe archivar cajas con sesión abierta; el test se revierte.
        self.env.cr.execute("UPDATE pos_config SET active = FALSE")
        self.env.invalidate_all()
        status, payload, _response = self._get_ready()

        self.assertEqual(status, 503)
        self.assertEqual(payload["checks"]["pos_config"], "missing")

    def test_database_failure_is_reported_without_details(self):
        def broken(self):
            raise RuntimeError("boom at C:/secret/path password=hunter2")

        with patch.object(type(self.env["vlux.core.system.info"]), "readiness", broken), \
                self.assertLogs("odoo.addons.vlux_core.controllers.health", "ERROR"):
            status, payload, _response = self._get_ready()

        self.assertEqual(status, 503)
        self.assertEqual(payload, {"status": "not_ready", "checks": {"database": "error"}})
        self._assert_safe(payload)

    def test_health_stays_minimal(self):
        response = self.url_open("/vlux/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.text), {"status": "ok"})
