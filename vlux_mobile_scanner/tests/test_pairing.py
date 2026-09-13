from datetime import timedelta

from psycopg2.errors import UniqueViolation

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from ..controllers.main import VluxMobileScannerController


@tagged("post_install", "-at_install")
class TestVluxMobileScannerPairing(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pos_config = cls.env["pos.config"].sudo().create(
            {"name": "Escáner - prueba de seguridad"}
        )
        cls.pos_session = cls.env["pos.session"].sudo().create(
            {
                "config_id": cls.pos_config.id,
                "user_id": cls.env.user.id,
            }
        )

    def test_pair_code_shape(self):
        pairing_model = self.env["vlux.mobile.scanner.pairing"]
        code = pairing_model._new_pair_code()
        self.assertEqual(len(code), 8)
        self.assertTrue(code.isalnum())

    def test_invalid_base_url_port_is_rejected(self):
        controller = VluxMobileScannerController()

        self.assertFalse(controller._normalize_base_url("https://pos.example.com:invalid"))

    def test_token_hash_is_stable_and_not_plaintext(self):
        pairing_model = self.env["vlux.mobile.scanner.pairing"]
        token = "vlux-example-token"
        digest = pairing_model._hash_token(token)
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, token)
        self.assertEqual(digest, pairing_model._hash_token(token))

    def test_mobile_token_is_one_time_and_expires(self):
        pairing = self.env[
            "vlux.mobile.scanner.pairing"
        ].create_waiting_pairing(self.pos_session, "test-device")

        raw_token = pairing.issue_mobile_token()

        self.assertTrue(raw_token)
        self.assertNotEqual(pairing.token_hash, raw_token)
        self.assertEqual(pairing.state, "paired")
        self.assertFalse(pairing.issue_mobile_token())
        self.assertEqual(
            pairing,
            pairing.authenticate_mobile_token(raw_token),
        )

        pairing.sudo().write(
            {
                "token_expires_at": fields.Datetime.now()
                - timedelta(seconds=1)
            }
        )
        self.assertFalse(pairing.authenticate_mobile_token(raw_token))
        self.assertEqual(pairing.state, "expired")

    def test_expired_pairing_cannot_issue_token(self):
        pairing = self.env[
            "vlux.mobile.scanner.pairing"
        ].create_waiting_pairing(self.pos_session, "expired-device")
        pairing.sudo().write(
            {
                "pair_expires_at": fields.Datetime.now()
                - timedelta(seconds=1)
            }
        )

        self.assertFalse(pairing.issue_mobile_token())
        self.assertEqual(pairing.state, "expired")

    def test_invalid_mobile_token_is_rejected(self):
        pairing_model = self.env["vlux.mobile.scanner.pairing"]

        self.assertFalse(pairing_model.authenticate_mobile_token(""))
        self.assertFalse(pairing_model.authenticate_mobile_token("invalid-token"))
        self.assertFalse(pairing_model.authenticate_mobile_token("x" * 300))

    def test_request_id_is_idempotency_key(self):
        pairing = self.env[
            "vlux.mobile.scanner.pairing"
        ].create_waiting_pairing(self.pos_session, "idempotency-device")
        values = {
            "request_id": "req-idempotent",
            "pairing_id": pairing.id,
            "barcode": "7501234567890",
            "device_identifier": pairing.device_identifier,
        }
        self.env["vlux.mobile.scanner.event"].sudo().create(values)

        with self.assertRaises(UniqueViolation), self.env.cr.savepoint():
            self.env["vlux.mobile.scanner.event"].sudo().create(values)

    def test_rate_limit_blocks_excess_requests(self):
        limiter = self.env["vlux.mobile.scanner.rate.limit"].sudo()

        self.assertTrue(limiter.consume("test", "device", 2, 60))
        self.assertTrue(limiter.consume("test", "device", 2, 60))
        self.assertFalse(limiter.consume("test", "device", 2, 60))
