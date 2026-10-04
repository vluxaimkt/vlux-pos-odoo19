"""The phone scanner for a register on the VLUX API (the PWA): pair, receive scans, answer them."""
import json
from datetime import timedelta

from odoo.tests import HttpCase, tagged

from odoo.addons.vlux_core.tests.test_api_v1 import API, VluxApiCase


@tagged("post_install", "-at_install")
class TestVluxApiMobileScanner(HttpCase, VluxApiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["pos.config"].sudo().create({"name": "Caja PWA escáner"})
        cls.other = cls.env["pos.config"].sudo().create({"name": "Otra caja"})
        cls.session = cls.env["pos.session"].sudo().create({"config_id": cls.config.id, "user_id": cls.env.user.id})
        cashier = cls._make_user("api-scanner-cashier", "vlux_core.group_vlux_cashier")
        _token, cls.raw = cls.env["vlux.api.token"].issue(
            "Caja PWA", "system:read catalog:read orders:write", user=cashier, pos_config=cls.config,
        )

    def _api(self, method, path, body=None):
        headers = {"Authorization": "Bearer " + self.raw}
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self.url_open(API + path, data=json.dumps(body) if body is not None else None,
                                 headers=headers, method=method)
        return response, response.json()

    def _phone(self, path, payload, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        return self.url_open(path, data=json.dumps(payload), headers=headers).json()

    def test_a_phone_scans_into_the_register_and_sees_the_result(self):
        base = "/registers/%d/scanner" % self.config.id
        response, body = self._api("POST", base + "/pair", {"device_id": "caja-pwa-0001"})
        self.assertEqual(response.status_code, 200, body)
        pairing = body["data"]
        self.assertTrue(pairing["qr_data_uri"].startswith("data:image/png;base64,"))
        self.assertIn("pair=" + pairing["code"], pairing["scanner_url"])

        phone = self._phone("/vlux/mobile/pair", {"code": pairing["code"]})
        self.assertTrue(phone["ok"], phone)
        query = "?pairing_id=%d&device_id=caja-pwa-0001" % pairing["pairing_id"]
        _response, status = self._api("GET", base + "/status" + query)
        self.assertEqual(status["data"]["status"], "paired")

        scan = self._phone("/vlux/mobile/scan", {"barcode": "7501055300846"}, token=phone["token"])
        _response, events = self._api("GET", base + "/events" + query)
        self.assertEqual(events["data"]["items"], [{"request_id": scan["request_id"], "barcode": "7501055300846"}])

        _response, wrong = self._api("GET", base + "/events?pairing_id=%d&device_id=otro-equipo-99" % pairing["pairing_id"])
        self.assertEqual(wrong["error"], "NOT_FOUND", "another device of the store does not get this phone's scans")

        response, acked = self._api("POST", base + "/ack", {
            "device_id": "caja-pwa-0001", "request_id": scan["request_id"], "status": "delivered",
            "result_code": "ADDED_TO_CART", "product_name": "Refresco", "unit_price": 18.0,
        })
        self.assertEqual(response.status_code, 200, acked)
        result = self._phone("/vlux/mobile/result", {"request_id": scan["request_id"]}, token=phone["token"])
        self.assertEqual((result["status"], result["product"]["name"]), ("delivered", "Refresco"))
        _response, events = self._api("GET", base + "/events" + query)
        self.assertEqual(events["data"]["items"], [], "an answered scan is not delivered again")

        self._api("POST", base + "/revoke", {"pairing_id": pairing["pairing_id"], "device_id": "caja-pwa-0001"})
        self.assertFalse(self._phone("/vlux/mobile/scan", {"barcode": "1"}, token=phone["token"]).get("ok"))

    def test_a_stale_scan_is_answered_as_lost_not_added_later(self):
        base = "/registers/%d/scanner" % self.config.id
        _response, body = self._api("POST", base + "/pair", {"device_id": "caja-pwa-0002"})
        phone = self._phone("/vlux/mobile/pair", {"code": body["data"]["code"]})
        scan = self._phone("/vlux/mobile/scan", {"barcode": "7501055300846"}, token=phone["token"])
        event = self.env["vlux.mobile.scanner.event"].sudo().search([("request_id", "=", scan["request_id"])])
        self.env.cr.execute("UPDATE vlux_mobile_scanner_event SET create_date = create_date - interval '10 minutes' WHERE id = %s",
                            [event.id])
        event.invalidate_recordset()
        _response, events = self._api("GET", base + "/events?pairing_id=%d&device_id=caja-pwa-0002" % body["data"]["pairing_id"])
        self.assertEqual(events["data"]["items"], [])
        event.invalidate_recordset()
        self.assertEqual((event.state, event.result_code), ("failed", "EXPIRED"))

    def test_a_register_token_cannot_pair_another_register(self):
        response, body = self._api("POST", "/registers/%d/scanner/pair" % self.other.id, {"device_id": "caja-pwa-0003"})
        self.assertEqual((response.status_code, body["error"]), (403, "FORBIDDEN"))
        response, body = self._api("POST", "/registers/%d/scanner/pair" % self.config.id, {"device_id": "x"})
        self.assertEqual(body["error"], "VALIDATION_ERROR")
