from datetime import timedelta

from psycopg2.errors import UniqueViolation

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestVluxFiscalSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pos_config = cls.env["pos.config"].sudo().create(
            {"name": "Facturación - prueba de seguridad"}
        )
        cls.pos_session = cls.env["pos.session"].sudo().create(
            {
                "config_id": cls.pos_config.id,
                "user_id": cls.env.user.id,
            }
        )
        cls.pos_order = cls.env["pos.order"].sudo().create(
            {
                "session_id": cls.pos_session.id,
                "pricelist_id": cls.pos_config.pricelist_id.id,
                "amount_paid": 0,
                "amount_return": 0,
                "amount_tax": 0,
                "amount_total": 0,
            }
        )
        cls.fiscal_request = cls.env["vlux.fiscal.request"].sudo().create(
            {"pos_order_id": cls.pos_order.id}
        )

    def test_public_token_is_hashed_rotated_and_revocable(self):
        raw_link = self.fiscal_request.issue_public_link_token()

        self.assertNotIn("access_token", self.fiscal_request._fields)
        self.assertNotEqual(self.fiscal_request.access_token_hash, raw_link)
        self.assertEqual(
            self.fiscal_request,
            self.fiscal_request.authenticate_public_token(raw_link),
        )

        record, session_token = self.fiscal_request.exchange_public_link_token(
            raw_link
        )

        self.assertEqual(record, self.fiscal_request)
        self.assertTrue(session_token)
        self.assertFalse(
            self.fiscal_request.authenticate_public_token(raw_link)
        )
        self.assertEqual(
            self.fiscal_request,
            self.fiscal_request.authenticate_public_token(
                session_token,
                request_id=self.fiscal_request.id,
            ),
        )

        self.fiscal_request.revoke_public_token()
        self.assertFalse(
            self.fiscal_request.authenticate_public_token(session_token)
        )

    def test_public_link_token_cannot_be_reused(self):
        raw_link = self.fiscal_request.issue_public_link_token()
        record, session_token = self.fiscal_request.exchange_public_link_token(
            raw_link
        )

        self.assertEqual(record, self.fiscal_request)
        self.assertTrue(session_token)
        reused_record, reused_session_token = (
            self.fiscal_request.exchange_public_link_token(raw_link)
        )
        self.assertFalse(reused_record)
        self.assertFalse(reused_session_token)

    def test_expired_public_token_is_rejected(self):
        raw_token = self.fiscal_request.issue_public_link_token()
        self.fiscal_request.sudo().write(
            {
                "token_expires_at": fields.Datetime.now()
                - timedelta(seconds=1)
            }
        )

        self.assertFalse(
            self.fiscal_request.authenticate_public_token(raw_token)
        )

    def test_pos_order_constraint_rejects_duplicate_request(self):
        with self.assertRaises(UniqueViolation), self.env.cr.savepoint():
            self.env["vlux.fiscal.request"].sudo().create(
                {"pos_order_id": self.pos_order.id}
            )

    def test_company_constraint_rejects_duplicate_config(self):
        provider = self.env.ref(
            "vlux_facturacion.vlux_pac_provider_simulator"
        )
        with self.assertRaises(UniqueViolation), self.env.cr.savepoint():
            self.env["vlux.fiscal.config"].sudo().create(
                {
                    "company_id": self.env.company.id,
                    "pac_provider_id": provider.id,
                }
            )

    def test_provider_code_constraint_rejects_duplicate(self):
        with self.assertRaises(UniqueViolation), self.env.cr.savepoint():
            self.env["vlux.pac.provider"].sudo().create(
                {
                    "name": "Proveedor duplicado",
                    "code": "vlux_simulator",
                }
            )

    def test_simulation_mode_processes_with_simulator(self):
        config = self.env.ref("vlux_facturacion.vlux_fiscal_config_main_company")
        config.sudo().write(
            {
                "fiscal_name": "VLUX Simulacion",
                "mode": "simulation",
            }
        )
        config.action_validate_configuration()
        self.fiscal_request.sudo().write(
            {
                "config_id": config.id,
                "pac_provider_id": config.pac_provider_id.id,
                "fiscal_name": "Cliente Simulado",
            }
        )

        self.fiscal_request.action_process_automatically()

        self.assertEqual(self.fiscal_request.state, "simulation_completed")
        self.assertTrue(self.fiscal_request.simulation_reference)
        self.assertFalse(self.fiscal_request.fiscal_uuid)

    def test_production_mode_rejects_simulator_provider(self):
        config = self.env.ref("vlux_facturacion.vlux_fiscal_config_main_company")
        config.sudo().write(
            {
                "mode": "production",
                "fiscal_name": "VLUX Produccion",
                "vat": "XAXX010101000",
                "fiscal_zip": "01000",
                "fiscal_regime": "601",
            }
        )

        with self.assertRaises(ValidationError):
            config.action_validate_configuration()

    def test_custom_provider_without_adapter_fails_controlled(self):
        provider = self.env["vlux.pac.provider"].sudo().create(
            {
                "name": "PAC Futuro",
                "code": "future_pac",
                "provider_type": "custom",
            }
        )

        with self.assertRaises(UserError):
            provider.validate_credentials()

        with self.assertRaises(UserError):
            provider.stamp_request(self.fiscal_request)

    def test_rate_limit_blocks_excess_requests(self):
        limiter = self.env["vlux.fiscal.rate.limit"].sudo()

        self.assertTrue(limiter.consume("test", "client", 2, 60))
        self.assertTrue(limiter.consume("test", "client", 2, 60))
        self.assertFalse(limiter.consume("test", "client", 2, 60))
