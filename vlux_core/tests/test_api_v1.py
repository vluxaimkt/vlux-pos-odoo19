import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import HttpCase, TransactionCase, tagged

from odoo.addons.vlux_core.controllers import api as api_controller

API = "/vlux/api/v1"


class VluxApiCase:
    @classmethod
    def _make_user(cls, login, group_xmlid):
        return (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .sudo()
            .create({
                "name": login,
                "login": login,
                "email": f"{login}@example.test",
                "company_id": cls.env.company.id,
                "company_ids": [(6, 0, cls.env.company.ids)],
                "group_ids": [(6, 0, [cls.env.ref("base.group_user").id, cls.env.ref(group_xmlid).id])],
            })
        )


@tagged("post_install", "-at_install")
class TestVluxApiToken(TransactionCase, VluxApiCase):
    """A token never grants more than the user behind it, and is stored hashed."""

    def test_token_is_stored_hashed_and_returned_once(self):
        owner = self._make_user("api-owner", "vlux_core.group_vlux_owner")
        token, raw = self.env["vlux.api.token"].issue("Caja 1", "catalog:read orders:write", user=owner)

        self.assertNotIn(raw, token.sudo().read(["token_hash", "token_prefix", "name"])[0].values())
        self.assertEqual(token.sudo().token_hash, self.env["vlux.api.token"]._hash(raw))
        self.assertTrue(raw.startswith(token.token_prefix))
        self.assertEqual(token.scopes, "catalog:read orders:write")

    def test_scopes_are_limited_by_the_vlux_role(self):
        cashier = self._make_user("api-cashier", "vlux_core.group_vlux_cashier")

        token, _raw = self.env["vlux.api.token"].issue("Caja cajero", "orders:write", user=cashier)
        self.assertTrue(token.has_scope("orders:write"))

        with self.assertRaises(UserError):
            self.env["vlux.api.token"].issue("Demasiado", "catalog:write", user=cashier)
        with self.assertRaises(UserError):
            self.env["vlux.api.token"].issue("Inventado", "catalog:fly", user=cashier)

    def test_expired_revoked_and_disabled_tokens_stop_working(self):
        owner = self._make_user("api-owner-2", "vlux_core.group_vlux_owner")
        Token = self.env["vlux.api.token"]

        token, raw = Token.issue("Vigente", "system:read", user=owner)
        self.assertEqual(Token.authenticate(raw), token)

        token.sudo().expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.assertFalse(Token.authenticate(raw))

        token.sudo().write({"expires_at": False, "active": False})
        self.assertFalse(Token.authenticate(raw), "a revoked token must not authenticate")

        token.sudo().write({"active": True})
        owner.sudo().active = False
        self.assertFalse(Token.authenticate(raw), "a disabled user must not authenticate")


@tagged("post_install", "-at_install")
class TestVluxApiTokenWizard(TransactionCase, VluxApiCase):
    """An operator issues and revokes tokens without touching the shell."""

    def test_wizard_issues_a_working_token_and_shows_it_once(self):
        cashier = self._make_user("api-wizard-cashier", "vlux_core.group_vlux_cashier")
        wizard = self.env["vlux.api.token.new"].create({
            "name": "Caja 2 — tablet",
            "user_id": cashier.id,
            "scope_orders_write": True,
            "scope_catalog_read": True,
        })

        wizard.action_issue()

        token = wizard.token_id
        self.assertEqual(token.scopes, "catalog:read orders:write")
        self.assertEqual(self.env["vlux.api.token"].authenticate(wizard.raw_token), token)
        self.assertNotIn(wizard.raw_token, token.sudo().read(["token_hash", "token_prefix"])[0].values())

        token.action_revoke()
        self.assertFalse(token.active)
        self.assertFalse(self.env["vlux.api.token"].authenticate(wizard.raw_token))

    def test_wizard_refuses_no_scope_and_scopes_above_the_role(self):
        cashier = self._make_user("api-wizard-cashier-2", "vlux_core.group_vlux_cashier")
        wizard = self.env["vlux.api.token.new"].create({"name": "Vacío", "user_id": cashier.id})

        with self.assertRaises(UserError):
            wizard.action_issue()

        wizard.scope_catalog_write = True
        with self.assertRaises(UserError):
            wizard.action_issue()
        self.assertFalse(wizard.raw_token)
        self.assertIn("catalog:read", wizard.allowed_scopes)
        self.assertNotIn("catalog:write", wizard.allowed_scopes)


@tagged("post_install", "-at_install")
class TestVluxApiV1Contract(HttpCase, VluxApiCase):
    """The contract every VLUX interface depends on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls._make_user("api-contract-owner", "vlux_core.group_vlux_owner")
        cls.token, cls.raw = cls.env["vlux.api.token"].issue(
            "Caja contrato", "system:read dashboard:read", user=cls.owner
        )
        cls.env.flush_all()

    def _get(self, path, token=None, headers=None):
        all_headers = dict(headers or {})
        if token:
            all_headers["Authorization"] = "Bearer " + token
        response = self.url_open(API + path, headers=all_headers)
        return response, json.loads(response.text)

    def test_me_returns_identity_scopes_and_store(self):
        response, body = self._get("/me", self.raw)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["token"]["scopes"], ["dashboard:read", "system:read"])
        # The endpoint ran as the token's user, not as the public user.
        self.assertEqual(body["data"]["user"]["login"], "api-contract-owner")
        self.assertEqual(body["data"]["company"]["id"], self.owner.company_id.id)
        self.assertEqual(body["data"]["company"]["currency"], self.env.company.currency_id.name)
        self.assertEqual(response.headers["X-Vlux-Api-Version"], "v1")
        self.assertEqual(response.headers["X-Request-Id"], body["request_id"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn(self.raw, response.text, "the token must never be echoed back")

    def test_missing_and_invalid_tokens_are_refused(self):
        response, body = self._get("/me")
        self.assertEqual((response.status_code, body["error"]), (401, "MISSING_TOKEN"))
        self.assertFalse(body["ok"])
        self.assertIn("request_id", body)

        response, body = self._get("/me", "not-a-real-token")
        self.assertEqual((response.status_code, body["error"]), (401, "INVALID_TOKEN"))

        response, body = self._get("/me", headers={"Authorization": "Basic " + self.raw})
        self.assertEqual((response.status_code, body["error"]), (401, "MISSING_TOKEN"))

    def test_scope_is_enforced(self):
        token, raw = self.env["vlux.api.token"].issue("Sin system", "dashboard:read", user=self.owner)
        self.env.flush_all()

        response, body = self._get("/me", raw)

        self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN_SCOPE"))
        self.assertIn("system:read", body["message"])
        self.assertTrue(token.has_scope("dashboard:read"))

    def test_rate_limit_answers_429(self):
        with patch.object(api_controller, "RATE_LIMIT", 2):
            self._get("/me", self.raw)
            self._get("/me", self.raw)
            response, body = self._get("/me", self.raw)

        self.assertEqual((response.status_code, body["error"]), (429, "RATE_LIMITED"))

    def test_usage_is_recorded_once_a_minute(self):
        token = self.token.sudo()
        token.last_used_at = False
        self.env.flush_all()

        self._get("/me", self.raw)
        token.invalidate_recordset(["last_used_at"])
        first = token.last_used_at
        self.assertTrue(first, "the first call records the usage")

        self._get("/me", self.raw)
        token.invalidate_recordset(["last_used_at"])
        self.assertEqual(token.last_used_at, first, "calls inside the minute stay read-only")

        stale = first - timedelta(minutes=5)
        token.last_used_at = stale
        self.env.flush_all()
        self._get("/me", self.raw)
        token.invalidate_recordset(["last_used_at"])
        self.assertGreaterEqual(token.last_used_at, first, "a stale timestamp is refreshed")

    def test_rate_limit_counts_failed_requests_too(self):
        """The counter commits on its own, so an errored request still counts."""
        def boom(self, token, **kwargs):
            raise RuntimeError("boom")

        failing = api_controller.api_route("/me", "system:read")(boom)
        with patch.object(api_controller, "RATE_LIMIT", 2):
            with patch.object(api_controller.VluxApiV1, "me", failing),                     self.assertLogs("odoo.addons.vlux_core.controllers.api", "ERROR"):
                response, body = self._get("/me", self.raw)
                self.assertEqual(response.status_code, 500)
                self._get("/me", self.raw)
            response, body = self._get("/me", self.raw)

        self.assertEqual((response.status_code, body["error"]), (429, "RATE_LIMITED"))

    def test_internal_errors_do_not_leak_details(self):
        def boom(self, token, **kwargs):
            raise RuntimeError("password=hunter2 at C:/secret")

        with patch.object(api_controller.VluxApiV1, "me", api_controller.api_route("/me", "system:read")(boom)), \
                self.assertLogs("odoo.addons.vlux_core.controllers.api", "ERROR"):
            response, body = self._get("/me", self.raw)

        self.assertEqual((response.status_code, body["error"]), (500, "INTERNAL_ERROR"))
        self.assertNotIn("hunter2", response.text)
        self.assertNotIn("secret", response.text)

    def test_openapi_describes_the_contract(self):
        response = self.url_open(API + "/openapi.json")
        spec = json.loads(response.text)["data"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertEqual(spec["servers"][0]["url"], API)
        self.assertIn("/me", spec["paths"])
        self.assertIn("system:read", spec["x-scopes"])
        for code in ("MISSING_TOKEN", "INVALID_TOKEN", "FORBIDDEN_SCOPE", "RATE_LIMITED"):
            self.assertIn(code, spec["components"]["schemas"]["Error"]["properties"]["error"]["enum"])


@tagged("post_install", "-at_install")
class TestVluxApiTokenRotation(TransactionCase, VluxApiCase):
    """A renewable token is replaced by the device before it expires."""

    def setUp(self):
        super().setUp()
        self.cashier = self._make_user("api-rotate-cashier", "vlux_core.group_vlux_cashier")
        self.Token = self.env["vlux.api.token"]

    def test_a_lifetime_sets_the_expiry(self):
        token, _raw = self.Token.issue("Caja", "orders:write", user=self.cashier, lifetime_days=30)
        remaining = token.expires_at - fields.Datetime.now()
        self.assertGreater(remaining, timedelta(days=29, hours=23))
        self.assertLessEqual(remaining, timedelta(days=30))

    def test_rotation_issues_a_twin_and_gives_the_old_one_a_grace_period(self):
        old, old_raw = self.Token.issue("Caja 1", "orders:write catalog:read", user=self.cashier, lifetime_days=30)

        new, new_raw = old.rotate()

        self.assertEqual(self.Token.authenticate(new_raw), new)
        self.assertEqual((new.name, new.scopes, new.user_id, new.lifetime_days),
                         (old.name, old.scopes, old.user_id, old.lifetime_days))
        self.assertEqual(old.rotated_to_id, new)
        self.assertEqual(self.Token.authenticate(old_raw), old, "the old token still works during the grace period")
        self.assertLessEqual(old.expires_at, fields.Datetime.now() + timedelta(hours=24))
        self.assertGreater(new.expires_at, old.expires_at)

    def test_asking_again_revokes_the_unused_replacement(self):
        old, _raw = self.Token.issue("Caja 1", "orders:write", user=self.cashier, lifetime_days=30)
        first, first_raw = old.rotate()
        second, second_raw = old.rotate()

        self.assertFalse(self.Token.authenticate(first_raw), "a lost, never used replacement is revoked")
        self.assertEqual(self.Token.authenticate(second_raw), second)

        second.sudo().last_used_at = fields.Datetime.now()
        third, _third_raw = old.rotate()
        self.assertTrue(second.active, "a replacement already in use is never revoked by a retry")
        self.assertEqual(old.rotated_to_id, third)

    def test_non_renewable_tokens_and_lost_roles_do_not_rotate(self):
        fixed, _raw = self.Token.issue("Fijo", "orders:write", user=self.cashier)
        with self.assertRaises(UserError):
            fixed.rotate()

        renewable, _raw = self.Token.issue("Caja", "orders:write", user=self.cashier, lifetime_days=30)
        self.cashier.sudo().group_ids = [(6, 0, [self.env.ref("base.group_user").id])]
        with self.assertRaises(UserError, msg="the scopes are checked again against the current role"):
            renewable.rotate()


@tagged("post_install", "-at_install")
class TestVluxApiTokenRotationHttp(HttpCase, VluxApiCase):

    def test_me_describes_the_token_and_rotate_returns_a_working_one_once(self):
        cashier = self._make_user("api-rotate-http", "vlux_core.group_vlux_cashier")
        token, raw = self.env["vlux.api.token"].issue("Caja", "system:read", user=cashier, lifetime_days=30)

        response = self.url_open(API + "/me", headers={"Authorization": "Bearer " + raw})
        me = json.loads(response.text)["data"]["token"]
        self.assertTrue(me["renewable"])
        self.assertIsNone(me["register_id"])
        self.assertTrue(me["expires_at"].endswith("Z"))

        response = self.url_open(API + "/token/rotate", data="{}", method="POST",
                                 headers={"Authorization": "Bearer " + raw, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Cache-Control"].split(",")[0], "no-store")
        data = json.loads(response.text)["data"]
        self.assertNotEqual(data["token"], raw)
        self.assertEqual(data["scopes"], ["system:read"])

        response = self.url_open(API + "/me", headers={"Authorization": "Bearer " + data["token"]})
        self.assertEqual(response.status_code, 200)

        fixed, fixed_raw = self.env["vlux.api.token"].issue("Fijo", "system:read", user=cashier)
        response = self.url_open(API + "/token/rotate", data="{}", method="POST",
                                 headers={"Authorization": "Bearer " + fixed_raw})
        self.assertEqual(json.loads(response.text)["error"], "VALIDATION_ERROR")
