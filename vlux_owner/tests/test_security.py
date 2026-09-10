from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestVluxOwnerSecurity(TransactionCase):
    def test_rate_limit_blocks_excess_requests(self):
        limiter = self.env["vlux.owner.rate.limit"].sudo()

        self.assertTrue(limiter.consume("test", self.env.user.id, 2, 60))
        self.assertTrue(limiter.consume("test", self.env.user.id, 2, 60))
        self.assertFalse(limiter.consume("test", self.env.user.id, 2, 60))
