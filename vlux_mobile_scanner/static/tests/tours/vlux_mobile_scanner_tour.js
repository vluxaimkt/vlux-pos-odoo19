import { registry } from "@web/core/registry";
import * as ProductScreen from "@point_of_sale/../tests/pos/tours/utils/product_screen_util";
import * as Chrome from "@point_of_sale/../tests/pos/tours/utils/chrome_util";
import * as Dialog from "@point_of_sale/../tests/generic_helpers/dialog_util";

/*
 * End-to-end of the mobile scanner protocol with the POS running in the
 * browser under test and the phone simulated from the same page:
 *
 *   phone POST /vlux/mobile/scan  ->  Odoo bus  ->  POS barcodeReader.scan()
 *   -> POST /vlux/pos/ack -> bus push to the phone channel (+ batch poll fallback)
 *
 * Odoo enables websockets while a tour runs, so both bus hops are real.
 */

const KNOWN_BARCODE = "7509992000019";
const UNKNOWN_BARCODE = "7509992000026";
const RESULT_TIMEOUT_MS = 8000;

function openRegister() {
    return [Chrome.startPoS(), Dialog.confirm("Open Register")].flat();
}

async function postJson(path, payload, token) {
    const headers = { "Content-Type": "application/json" };
    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }
    const response = await fetch(path, { method: "POST", headers, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
        throw new Error(`${path} -> ${response.status} ${data.message || ""}`);
    }
    return data;
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Pair a simulated phone and subscribe it to its push channel over the bus websocket. */
async function pairPhone() {
    const pos = window.posmodel;
    const pairing = await pos.vluxCreateMobilePairing();
    const paired = await postJson("/vlux/mobile/pair", { code: pairing.code });
    const phone = {
        token: paired.token,
        pushChannel: paired.push_channel,
        pushed: new Map(),
        socketOpen: false,
        transports: [],
    };
    window.__vluxScannerTour = phone;
    if (!phone.pushChannel) {
        throw new Error("pairing did not return a push channel");
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/websocket?version=${paired.push_version}`);
    socket.addEventListener("open", () => {
        phone.socketOpen = true;
        socket.send(JSON.stringify({ event_name: "subscribe", data: { channels: [phone.pushChannel], last: 0 } }));
    });
    socket.addEventListener("message", (event) => {
        for (const notification of JSON.parse(event.data)) {
            const message = notification?.message;
            if (message?.type !== "VLUX_MOBILE_RESULT") {
                continue;
            }
            for (const row of message.payload?.results || []) {
                phone.pushed.set(row.request_id, row);
            }
        }
    });
    const startedAt = Date.now();
    while (!phone.socketOpen && Date.now() - startedAt < 5000) {
        await sleep(50);
    }
    return phone;
}

/** Scan like a phone: POST the barcode, then wait for the push or the batch poll. */
async function scanFromPhone(barcode, { requestId = crypto.randomUUID() } = {}) {
    const phone = window.__vluxScannerTour;
    await postJson("/vlux/mobile/scan", { barcode, request_id: requestId }, phone.token);
    const startedAt = Date.now();
    let nextPoll = startedAt + 300;
    while (Date.now() - startedAt < RESULT_TIMEOUT_MS) {
        const pushed = phone.pushed.get(requestId);
        if (pushed) {
            phone.transports.push("push");
            return pushed;
        }
        if (Date.now() >= nextPoll) {
            const data = await postJson("/vlux/mobile/results", { request_ids: [requestId] }, phone.token);
            const row = data.results[0];
            if (row && row.status !== "queued" && row.status !== "unknown") {
                phone.transports.push("poll");
                return row;
            }
            nextPoll = Date.now() + 300;
        }
        await sleep(30);
    }
    throw new Error(`no result for ${barcode} within ${RESULT_TIMEOUT_MS} ms`);
}

function step(content, run) {
    return { content, trigger: "body", run };
}

registry.category("web_tour.tours").add("VluxScannerKnownBarcodeTour", {
    steps: () =>
        [
            openRegister(),
            step("pair a phone", pairPhone),
            step("phone scans a known barcode", async () => {
                const result = await scanFromPhone(KNOWN_BARCODE);
                if (result.status !== "delivered" || result.result_code !== "ADDED_TO_CART") {
                    throw new Error("unexpected result " + JSON.stringify(result));
                }
            }),
            ProductScreen.selectedOrderlineHas("Agua Scanner 1L", 1),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxScannerPushDeliveryTour", {
    steps: () =>
        [
            openRegister(),
            step("pair a phone", pairPhone),
            step("results reach the phone through the bus push, not polling", async () => {
                const phone = window.__vluxScannerTour;
                if (!phone.socketOpen) {
                    throw new Error("phone websocket did not open");
                }
                for (let i = 0; i < 3; i += 1) {
                    await scanFromPhone(KNOWN_BARCODE);
                }
                const pushes = phone.transports.filter((t) => t === "push").length;
                if (pushes < 2) {
                    throw new Error("expected push delivery, transports were " + phone.transports.join(","));
                }
            }),
            ProductScreen.selectedOrderlineHas("Agua Scanner 1L", 3),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxScannerRepeatedBarcodeTour", {
    steps: () =>
        [
            openRegister(),
            step("pair a phone", pairPhone),
            step("phone scans the same barcode five times on purpose", async () => {
                for (let i = 0; i < 5; i += 1) {
                    const result = await scanFromPhone(KNOWN_BARCODE);
                    if (result.status !== "delivered") {
                        throw new Error("scan " + i + " -> " + JSON.stringify(result));
                    }
                }
            }),
            ProductScreen.selectedOrderlineHas("Agua Scanner 1L", 5),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxScannerBurstTour", {
    steps: () =>
        [
            openRegister(),
            step("pair a phone", pairPhone),
            step("ten scans fired without waiting for each other all land", async () => {
                const phone = window.__vluxScannerTour;
                const ids = [];
                for (let i = 0; i < 10; i += 1) {
                    const requestId = crypto.randomUUID();
                    ids.push(requestId);
                    await postJson("/vlux/mobile/scan", { barcode: KNOWN_BARCODE, request_id: requestId }, phone.token);
                }
                const startedAt = Date.now();
                let resolved = new Set();
                while (resolved.size < ids.length && Date.now() - startedAt < RESULT_TIMEOUT_MS * 2) {
                    for (const id of ids) {
                        if (phone.pushed.get(id)?.status === "delivered") {
                            resolved.add(id);
                        }
                    }
                    if (resolved.size < ids.length) {
                        const data = await postJson("/vlux/mobile/results", { request_ids: ids }, phone.token);
                        resolved = new Set([...resolved, ...data.results.filter((r) => r.status === "delivered").map((r) => r.request_id)]);
                        await sleep(300);
                    }
                }
                if (resolved.size !== ids.length) {
                    throw new Error(`only ${resolved.size}/${ids.length} scans delivered`);
                }
            }),
            ProductScreen.selectedOrderlineHas("Agua Scanner 1L", 10),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxScannerUnknownBarcodeTour", {
    steps: () =>
        [
            openRegister(),
            step("pair a phone", pairPhone),
            step("phone scans an unknown barcode", async () => {
                const result = await scanFromPhone(UNKNOWN_BARCODE);
                if (result.status !== "not_found") {
                    throw new Error("unexpected result " + JSON.stringify(result));
                }
            }),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxScannerPosNotReadyTour", {
    steps: () =>
        [
            openRegister(),
            step("pair a phone", pairPhone),
            Chrome.clickOrders(),
            step("phone scans while the POS is on the ticket screen", async () => {
                const result = await scanFromPhone(KNOWN_BARCODE);
                if (result.status !== "failed" || result.result_code !== "POS_NOT_READY") {
                    throw new Error("unexpected result " + JSON.stringify(result));
                }
            }),
            Chrome.endTour(),
        ].flat(),
});

registry.category("web_tour.tours").add("VluxScannerDuplicateDeliveryTour", {
    steps: () =>
        [
            openRegister(),
            step("pair a phone", pairPhone),
            step("the same bus payload delivered several times is processed once", async () => {
                const pos = window.posmodel;
                const phone = window.__vluxScannerTour;
                const requestId = crypto.randomUUID();
                const queued = await postJson(
                    "/vlux/mobile/scan",
                    { barcode: KNOWN_BARCODE, request_id: requestId },
                    phone.token
                );
                const payload = {
                    request_id: queued.request_id,
                    pos_config_id: pos.config.id,
                    pos_session_id: pos.session.id,
                    device_identifier: pos.device.identifier,
                    barcode: KNOWN_BARCODE,
                };
                // The bus delivers it too; every extra delivery must be a no-op.
                await pos._vluxOnMobileBarcode(payload);
                await pos._vluxOnMobileBarcode(payload);
                await scanFromPhone(KNOWN_BARCODE, { requestId }).catch(() => null);
                await sleep(800);
            }),
            ProductScreen.selectedOrderlineHas("Agua Scanner 1L", 1),
            Chrome.endTour(),
        ].flat(),
});
