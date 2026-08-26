from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestVluxMobileScannerPairing(TransactionCase):
    def test_pair_code_shape(self):
        pairing_model = self.env["vlux.mobile.scanner.pairing"]
        code = pairing_model._new_pair_code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isalnum())

    def test_token_hash_is_stable_and_not_plaintext(self):
        pairing_model = self.env["vlux.mobile.scanner.pairing"]
        token = "vlux-example-token"
        digest = pairing_model._hash_token(token)
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, token)
        self.assertEqual(digest, pairing_model._hash_token(token))
