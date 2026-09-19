import json
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged

from ..controllers.main import RATE_RESULTS, RATE_SCAN, RESULTS_BATCH_MAX
from ..models.pairing import LAST_SEEN_THROTTLE_SECONDS
from ..models.scan_event import PUSH_NOTIFICATION_TYPE


@tagged("post_install", "-at_install")
class TestVluxMobileScannerProtocol(HttpCase):
    """Phone <-> Odoo protocol through the real HTTP endpoints (no camera)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pos_config = cls.env["pos.config"].sudo().create({"name": "Scanner V2 protocol"})
        cls.pos_session = cls.env["pos.session"].sudo().create(
            {"config_id": cls.pos_config.id, "user_id": cls.env.user.id}
        )
        cls.product = cls.env["product.product"].sudo().create({
            "name": "Scanner V2 product",
            "barcode": "7509991000012",
            "list_price": 10.0,
            "available_in_pos": True,
        })

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _post(self, path, payload, token=None, status=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self.url_open(path, data=json.dumps(payload), headers=headers)
        if status is not None:
            self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def _pair(self, device="phone-a"):
        pairing = self.env["vlux.mobile.scanner.pairing"].create_waiting_pairing(self.pos_session, device)
        data = self._post("/vlux/mobile/pair", {"code": pairing.pair_code}, status=200)
        self.assertTrue(data["ok"])
        pairing.invalidate_recordset()
        return pairing, data["token"], data

    def _scan(self, token, barcode, request_id=None, status=202):
        payload = {"barcode": barcode}
        if request_id:
            payload["request_id"] = request_id
        return self._post("/vlux/mobile/scan", payload, token=token, status=status)

    def _events(self, pairing):
        return self.env["vlux.mobile.scanner.event"].sudo().search([("pairing_id", "=", pairing.id)], order="id")

    def _bus_rows(self, needle):
        self.env.cr.precommit.run()
        return self.env["bus.bus"].sudo().search([("message", "ilike", needle)])

    # ------------------------------------------------------------------
    # pairing / token lifecycle
    # ------------------------------------------------------------------

    def test_pairing_returns_push_descriptor(self):
        pairing, token, data = self._pair()
        self.assertEqual(pairing.state, "paired")
        self.assertTrue(data["push_channel"].startswith("vlux_mobile_scanner:"))
        self.assertEqual(data["push_channel"], pairing.push_channel)
        self.assertTrue(data["push_version"])
        self.assertEqual(data["results_batch_max"], RESULTS_BATCH_MAX)
        # The channel is derived from the hash, never equal to the token or its hash.
        self.assertNotIn(token, data["push_channel"])
        self.assertNotIn(pairing.token_hash, data["push_channel"])
        # Pair codes are single use.
        self._post("/vlux/mobile/pair", {"code": pairing.pair_code}, status=400)

    def test_invalid_expired_and_revoked_tokens_are_rejected(self):
        self._post("/vlux/mobile/scan", {"barcode": "123456"}, token="not-a-token", status=401)
        pairing, token, _ = self._pair()
        self._scan(token, "7509991000012")
        pairing.sudo().write({"token_expires_at": fields.Datetime.now() - timedelta(seconds=1)})
        self._post("/vlux/mobile/scan", {"barcode": "123456"}, token=token, status=401)
        self.assertEqual(pairing.state, "expired")

        pairing_b, token_b, _ = self._pair("phone-b")
        pairing_b.action_revoke()
        self._post("/vlux/mobile/results", {"request_ids": ["x"]}, token=token_b, status=401)
        self.assertFalse(pairing_b.push_channel)

    def test_disconnect_revokes(self):
        pairing, token, _ = self._pair()
        data = self._post("/vlux/mobile/disconnect", {}, token=token, status=200)
        self.assertTrue(data["revoked"])
        self.assertEqual(pairing.state, "revoked")
        self._post("/vlux/mobile/heartbeat", {}, token=token, status=401)

    def test_authentication_does_not_write_on_every_request(self):
        pairing, token, _ = self._pair()
        seen = pairing.last_seen_at
        for _ in range(5):
            self._post("/vlux/mobile/results", {"request_ids": ["a"]}, token=token, status=200)
        pairing.invalidate_recordset()
        self.assertEqual(pairing.last_seen_at, seen, "hot path must not touch last_seen_at")
        # Once the throttle window passed, the next authenticated call refreshes it.
        pairing.sudo().write({"last_seen_at": seen - timedelta(seconds=LAST_SEEN_THROTTLE_SECONDS + 1)})
        self._post("/vlux/mobile/results", {"request_ids": ["a"]}, token=token, status=200)
        pairing.invalidate_recordset()
        self.assertGreater(pairing.last_seen_at, seen - timedelta(seconds=LAST_SEEN_THROTTLE_SECONDS))
        # Heartbeats always refresh presence.
        pairing.sudo().write({"last_seen_at": seen - timedelta(seconds=5)})
        self._post("/vlux/mobile/heartbeat", {}, token=token, status=200)
        pairing.invalidate_recordset()
        self.assertGreaterEqual(pairing.last_seen_at, seen)

    # ------------------------------------------------------------------
    # scans
    # ------------------------------------------------------------------

    def test_scan_notifies_pos_once_and_is_idempotent(self):
        pairing, token, _ = self._pair()
        request_id = str(uuid.uuid4())
        first = self._scan(token, "7509991000012", request_id)
        self.assertEqual(first["request_id"], request_id)
        self.assertFalse(first["duplicate"])
        # Duplicate network delivery of the same scan: same event, no new notification.
        second = self._scan(token, "7509991000012", request_id, status=200)
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["request_id"], request_id)
        self.assertEqual(len(self._events(pairing)), 1)
        self.assertEqual(len(self._bus_rows(request_id)), 1)

    def test_request_id_cannot_be_hijacked_by_another_pairing(self):
        _, token_a, _ = self._pair("phone-a")
        _, token_b, _ = self._pair("phone-b")
        request_id = str(uuid.uuid4())
        self._scan(token_a, "7509991000012", request_id)
        self._scan(token_b, "7509991000012", request_id, status=409)
        # A malformed client id is ignored and replaced by a server id.
        data = self._scan(token_b, "7509991000012", "not-a-uuid")
        self.assertNotEqual(data["request_id"], "not-a-uuid")

    def test_intentional_repeated_scan_creates_one_event_per_scan(self):
        pairing, token, _ = self._pair()
        for _ in range(5):
            self._scan(token, "7509991000012", str(uuid.uuid4()))
        events = self._events(pairing)
        self.assertEqual(len(events), 5)
        self.assertEqual(set(events.mapped("barcode")), {"7509991000012"})
        self.assertEqual(len(set(events.mapped("request_id"))), 5)

    def test_hundred_consecutive_scans_are_all_registered(self):
        pairing, token, _ = self._pair()
        ids = [str(uuid.uuid4()) for _ in range(100)]
        for index, request_id in enumerate(ids):
            self._scan(token, str(7509991000000 + index), request_id)
        events = self._events(pairing)
        self.assertEqual(len(events), 100)
        self.assertEqual(set(events.mapped("request_id")), set(ids))
        self.assertEqual(set(events.mapped("state")), {"queued"})
        self.assertGreaterEqual(RATE_SCAN, 200, "budget must allow fast scanning bursts")

    def test_invalid_barcode_and_closed_session(self):
        pairing, token, _ = self._pair()
        self._post("/vlux/mobile/scan", {"barcode": ""}, token=token, status=400)
        self._post("/vlux/mobile/scan", {"barcode": "x" * 200}, token=token, status=400)
        self.pos_session.sudo().write({"state": "closed"})
        try:
            self._post("/vlux/mobile/scan", {"barcode": "123"}, token=token, status=401)
            self.assertIn(pairing.state, ("revoked", "expired"))
        finally:
            self.pos_session.sudo().write({"state": "opening_control"})

    # ------------------------------------------------------------------
    # results: batch + push
    # ------------------------------------------------------------------

    def test_batch_results_and_push_notification(self):
        pairing, token, _ = self._pair()
        ids = [str(uuid.uuid4()) for _ in range(3)]
        for request_id in ids:
            self._scan(token, "7509991000012", request_id)
        data = self._post("/vlux/mobile/results", {"request_ids": ids + ["ghost"]}, token=token, status=200)
        self.assertEqual([row["status"] for row in data["results"]], ["queued", "queued", "queued", "unknown"])

        # The POS acknowledges the second scan: stored + pushed on the private channel.
        event = self._events(pairing).filtered(lambda e: e.request_id == ids[1])
        event.apply_pos_result({
            "state": "delivered",
            "result_code": "ADDED_TO_CART",
            "result_message": "ok",
            "product_id": self.product.id,
            "product_name": self.product.display_name,
            "unit_price": 10.0,
        })
        self.env.cr.precommit.run()
        pushed = self.env["bus.bus"].sudo().search([("channel", "ilike", pairing.push_channel)])
        self.assertEqual(len(pushed), 1)
        self.assertIn(PUSH_NOTIFICATION_TYPE, pushed.message)
        self.assertIn(ids[1], pushed.message)
        self.assertNotIn(token, pushed.message)

        data = self._post("/vlux/mobile/results", {"request_ids": ids}, token=token, status=200)
        rows = {row["request_id"]: row for row in data["results"]}
        self.assertEqual(rows[ids[1]]["status"], "delivered")
        self.assertEqual(rows[ids[1]]["product"]["name"], self.product.display_name)
        self.assertEqual(rows[ids[1]]["product"]["unit_price"], 10.0)
        self.assertEqual(rows[ids[0]]["status"], "queued")
        # The single-result endpoint stays available for old clients.
        single = self._post("/vlux/mobile/result", {"request_id": ids[1]}, token=token, status=200)
        self.assertEqual(single["status"], "delivered")

    def test_batch_results_validation(self):
        _, token, _ = self._pair()
        self._post("/vlux/mobile/results", {"request_ids": []}, token=token, status=400)
        self._post("/vlux/mobile/results", {"request_ids": "abc"}, token=token, status=400)
        self._post("/vlux/mobile/results", {"request_ids": [1, 2]}, token=token, status=400)
        too_many = [str(uuid.uuid4()) for _ in range(RESULTS_BATCH_MAX + 1)]
        self._post("/vlux/mobile/results", {"request_ids": too_many}, token=token, status=400)
        self.assertGreaterEqual(RATE_RESULTS, 60)

    def test_results_are_scoped_to_the_pairing(self):
        pairing_a, token_a, _ = self._pair("phone-a")
        _, token_b, _ = self._pair("phone-b")
        request_id = str(uuid.uuid4())
        self._scan(token_a, "7509991000012", request_id)
        data = self._post("/vlux/mobile/results", {"request_ids": [request_id]}, token=token_b, status=200)
        self.assertEqual(data["results"][0]["status"], "unknown")
        self._post("/vlux/mobile/result", {"request_id": request_id}, token=token_b, status=404)

    def test_pos_ack_requires_authenticated_pos_user(self):
        response = self.url_open(
            "/vlux/pos/ack",
            data='{"request_id": "x"}',
            headers={"Content-Type": "application/json"},
            allow_redirects=False,
        )
        self.assertNotEqual(response.status_code, 200)
