"""Benchmark the mobile scanner protocol: requests per scan and scan latency.

The tool plays the phone side against a running Odoo HTTP server and, through
the ORM, plays the POS side (acknowledging queued events after a configurable
processing delay). It never uses a real camera or browser; it measures the
network protocol itself so BEFORE/AFTER numbers are comparable.

Strategies (mirroring the JavaScript client):

* ``v1``    - one scan at a time, poll ``/vlux/mobile/result`` every 250 ms.
* ``batch`` - pipelined scans, one ``/vlux/mobile/results`` poll for all
              pending request ids with adaptive back-off.
* ``push``  - pipelined scans, results delivered over the Odoo bus websocket,
              with the batch poll only as a safety net.

    python tools/perf/bench_scanner.py -c odoo.conf -d vlux_perf \
        --base-url http://127.0.0.1:8069 --strategy v1 --scans 100
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _odoo_env import add_common_arguments, bootstrap, environment, percentile  # noqa: E402

SEED_PREFIX = "VLUXPERF"


# --------------------------------------------------------------------------
# POS simulator (ORM side)
# --------------------------------------------------------------------------


class PosSimulator(threading.Thread):
    """Acknowledge queued scanner events the way the POS browser would."""

    def __init__(self, odoo, db: str, pairing_id: int, ack_delay_ms: int, product_id: int) -> None:
        super().__init__(daemon=True)
        self.odoo = odoo
        self.db = db
        self.pairing_id = pairing_id
        self.ack_delay = ack_delay_ms / 1000.0
        self.product_id = product_id
        self.stop_event = threading.Event()
        self.acked = 0

    def run(self) -> None:
        from odoo import SUPERUSER_ID, api

        registry = self.odoo.modules.registry.Registry(self.db)
        while not self.stop_event.is_set():
            with registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                events = env["vlux.mobile.scanner.event"].search(
                    [("pairing_id", "=", self.pairing_id), ("state", "=", "queued")],
                    order="id asc",
                    limit=50,
                )
                if events:
                    time.sleep(self.ack_delay)
                    for event in events:
                        values = {
                            "state": "delivered",
                            "result_code": "ADDED_TO_CART",
                            "result_message": "bench",
                            "product_id": self.product_id,
                            "product_name": "Bench product",
                            "unit_price": 10.0,
                        }
                        if hasattr(event, "apply_pos_result"):
                            event.apply_pos_result(values)
                        else:
                            event.write({**values, "processed_at": env["vlux.mobile.scanner.event"]._fields["processed_at"].now()})
                        self.acked += 1
                    cr.commit()
            time.sleep(0.03)


# --------------------------------------------------------------------------
# Minimal RFC 6455 client (text frames only) for the push strategy
# --------------------------------------------------------------------------


class BusWebSocket:
    def __init__(self, base_url: str, version: str, db: str) -> None:
        parts = urlsplit(base_url)
        secure = parts.scheme == "https"
        host = parts.hostname
        port = parts.port or (443 if secure else 80)
        raw = socket.create_connection((host, port), timeout=10)
        if secure:
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        self.sock = raw
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET /websocket?version={version} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"Origin: {parts.scheme}://{host}:{port}\r\n"
            # No X-Odoo-Database here: a stateless (header) session cannot be persisted
            # and the bus dispatcher closes such sockets with 4001. Push therefore
            # needs a mono-db host or dbfilter, exactly like production.
            "User-Agent: vlux-bench\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("websocket handshake closed")
            response += chunk
        head, _, rest = response.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError("websocket handshake failed: " + head.decode(errors="replace"))
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        if expected.encode() not in head:
            raise RuntimeError("websocket accept mismatch")
        self.buffer = rest
        self.sock.settimeout(0.2)

    def send_text(self, payload: str) -> None:
        data = payload.encode()
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _read_exact(self, size: int) -> bytes | None:
        while len(self.buffer) < size:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                raise RuntimeError("websocket closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def receive_text(self) -> str | None:
        """Return one text message, or None on timeout."""
        while True:
            head = self._read_exact(2)
            if head is None:
                return None
            opcode = head[0] & 0x0F
            length = head[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            payload = self._read_exact(length) if length else b""
            if opcode == 0x1:
                return payload.decode()
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + b"\x00\x00\x00\x00")
            elif opcode == 0x8:
                raise RuntimeError("websocket closed by server")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Phone client strategies
# --------------------------------------------------------------------------


class PhoneClient:
    def __init__(self, base_url: str, db: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Odoo-Database": db,
        })
        self.requests = 0
        self.lock = threading.Lock()

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        with self.lock:
            self.requests += 1
        response = self.session.post(self.base_url + path, data=json.dumps(payload), timeout=30)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {}


def run_v1(client: PhoneClient, barcodes: list[str], timeout: float) -> dict:
    latencies = []
    outcomes = {}
    for barcode in barcodes:
        started = time.perf_counter()
        status, data = client.post("/vlux/mobile/scan", {"barcode": barcode})
        if status != 202:
            outcomes[barcode] = f"scan_http_{status}"
            continue
        request_id = data["request_id"]
        result = None
        while time.perf_counter() - started < timeout:
            time.sleep(0.25)
            status, data = client.post("/vlux/mobile/result", {"request_id": request_id})
            if status == 200 and data.get("status") != "queued":
                result = data
                break
        latencies.append((time.perf_counter() - started) * 1000.0)
        outcomes[barcode] = result["status"] if result else "timeout"
    return {"latencies_ms": latencies, "outcomes": outcomes}


def run_pipelined(client: PhoneClient, barcodes: list[str], timeout: float, scan_interval: float,
                  push: BusWebSocket | None, poll_min: float, poll_max: float) -> dict:
    pending: dict[str, float] = {}
    latencies: list[float] = []
    outcomes: dict[str, str] = {}
    lock = threading.Lock()
    done = threading.Event()

    def resolve(request_id: str, status: str) -> None:
        with lock:
            started = pending.pop(request_id, None)
        if started is not None:
            latencies.append((time.perf_counter() - started) * 1000.0)
            outcomes[request_id] = status

    def push_loop() -> None:
        while not done.is_set():
            try:
                message = push.receive_text()
            except RuntimeError:
                return
            if not message:
                continue
            for notification in json.loads(message):
                body = notification.get("message", {})
                if body.get("type") != "VLUX_MOBILE_RESULT":
                    continue
                for row in body.get("payload", {}).get("results", []):
                    resolve(row["request_id"], row["status"])

    def poll_loop() -> None:
        interval = poll_min
        while not done.is_set():
            with lock:
                ids = list(pending.keys())
            if not ids:
                interval = poll_min
                time.sleep(0.05)
                continue
            if push is not None:
                # Safety net only: poll requests that have been waiting for more than 3 s.
                now = time.perf_counter()
                with lock:
                    ids = [rid for rid, started in pending.items() if now - started > 3.0]
                if not ids:
                    time.sleep(0.1)
                    continue
            status, data = client.post("/vlux/mobile/results", {"request_ids": ids[:50]})
            resolved_any = False
            if status == 200:
                for row in data.get("results", []):
                    if row.get("status") != "queued":
                        resolve(row["request_id"], row["status"])
                        resolved_any = True
            interval = poll_min if resolved_any else min(poll_max, interval * 1.6)
            time.sleep(interval)

    threads = [threading.Thread(target=poll_loop, daemon=True)]
    if push is not None:
        threads.append(threading.Thread(target=push_loop, daemon=True))
    for thread in threads:
        thread.start()

    for barcode in barcodes:
        request_id = str(uuid.uuid4())
        with lock:
            pending[request_id] = time.perf_counter()
        status, data = client.post("/vlux/mobile/scan", {"barcode": barcode, "request_id": request_id})
        if status != 202:
            with lock:
                pending.pop(request_id, None)
            outcomes[request_id] = f"scan_http_{status}"
        time.sleep(scan_interval)

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        with lock:
            if not pending:
                break
        time.sleep(0.05)
    with lock:
        for request_id in list(pending):
            outcomes[request_id] = "timeout"
    done.set()
    return {"latencies_ms": latencies, "outcomes": outcomes}


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--base-url", default="http://127.0.0.1:8069")
    parser.add_argument("--strategy", choices=("v1", "batch", "push"), default="v1")
    parser.add_argument("--scans", type=int, default=50)
    parser.add_argument("--ack-delay-ms", type=int, default=150, help="Simulated POS processing time")
    parser.add_argument("--scan-interval-ms", type=int, default=120, help="Pipelined strategies only")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--poll-min-ms", type=int, default=300)
    parser.add_argument("--poll-max-ms", type=int, default=1500)
    parser.add_argument("--distinct-barcodes", type=int, default=0, help="0 = every scan a different barcode")
    parser.add_argument("--json", default="")
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    odoo = bootstrap(args)

    # --- pairing bootstrap through the ORM (no browser) -------------------
    with environment(odoo, args.db) as env:
        config = env["pos.config"].search([("name", "=", f"{SEED_PREFIX} Scanner"), ("company_id", "=", env.company.id)], limit=1)
        if not config:
            config = env["pos.config"].create({"name": f"{SEED_PREFIX} Scanner", "company_id": env.company.id})
        session = env["pos.session"].search([("config_id", "=", config.id), ("state", "in", ("opening_control", "opened"))], limit=1)
        if not session:
            session = env["pos.session"].create({"config_id": config.id, "user_id": env.uid})
        pairing = env["vlux.mobile.scanner.pairing"].create_waiting_pairing(session, "bench-device")
        token = pairing.issue_mobile_token()
        pairing_id = pairing.id
        push_channel = getattr(pairing, "push_channel", None)
        product = env["product.product"].search([("available_in_pos", "=", True)], limit=1)
        product_id = product.id
        version = None
        if args.strategy == "push":
            from odoo.addons.bus.websocket import WebsocketConnectionHandler

            version = WebsocketConnectionHandler._VERSION
        env.cr.commit()

    barcodes = [str(2000000000000 + i) for i in range(args.scans)]
    if args.distinct_barcodes:
        barcodes = [barcodes[i % args.distinct_barcodes] for i in range(args.scans)]

    simulator = PosSimulator(odoo, args.db, pairing_id, args.ack_delay_ms, product_id)
    simulator.start()
    client = PhoneClient(args.base_url, args.db, token)

    push = None
    if args.strategy == "push":
        if not push_channel:
            raise SystemExit("push strategy requires the scanner V2 push channel")
        push = BusWebSocket(args.base_url, version, args.db)
        push.send_text(json.dumps({"event_name": "subscribe", "data": {"channels": [push_channel], "last": 0}}))

    started = time.perf_counter()
    if args.strategy == "v1":
        result = run_v1(client, barcodes, args.timeout)
    else:
        result = run_pipelined(
            client, barcodes, args.timeout, args.scan_interval_ms / 1000.0, push,
            args.poll_min_ms / 1000.0, args.poll_max_ms / 1000.0,
        )
    elapsed = time.perf_counter() - started
    simulator.stop_event.set()
    if push is not None:
        push.close()

    with environment(odoo, args.db, readonly=True) as env:
        events = env["vlux.mobile.scanner.event"].search([("pairing_id", "=", pairing_id)])
        created = len(events)
        delivered = len(events.filtered(lambda e: e.state == "delivered"))
        by_barcode: dict[str, int] = {}
        for event in events:
            by_barcode[event.barcode] = by_barcode.get(event.barcode, 0) + 1
    expected_per_barcode: dict[str, int] = {}
    for code in barcodes:
        expected_per_barcode[code] = expected_per_barcode.get(code, 0) + 1
    lost = sum(max(0, expected_per_barcode[c] - by_barcode.get(c, 0)) for c in expected_per_barcode)
    duplicated = sum(max(0, by_barcode.get(c, 0) - expected_per_barcode[c]) for c in expected_per_barcode)

    outcomes = list(result["outcomes"].values())
    latencies = result["latencies_ms"]
    report = {
        "label": args.label or f"scanner_{args.strategy}",
        "strategy": args.strategy,
        "scans": args.scans,
        "ack_delay_ms": args.ack_delay_ms,
        "elapsed_s": round(elapsed, 2),
        "http_requests_total": client.requests,
        "http_requests_per_scan": round(client.requests / max(args.scans, 1), 2),
        "scan_to_result_ms": {
            "p50": round(percentile(latencies, 0.5), 1),
            "p95": round(percentile(latencies, 0.95), 1),
            "max": round(max(latencies), 1) if latencies else None,
        },
        "results_received": len(latencies),
        "timeouts": outcomes.count("timeout"),
        "rejected": sum(1 for o in outcomes if o.startswith("scan_http_")),
        "events_created": created,
        "events_delivered": delivered,
        "scans_lost": lost,
        "scans_duplicated": duplicated,
        "throughput_scans_per_s": round(args.scans / elapsed, 2) if elapsed else None,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
