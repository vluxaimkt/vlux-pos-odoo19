// Run with: node --test vlux_mobile_scanner/static/tests/node/
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const core = require(path.join(__dirname, "..", "..", "src", "scanner", "scanner_core.js"));
const { ScanGate, PendingQueue, PollBackoff, retryDelayMs, newRequestId, cropPlan, classifySendError } = core;

test("a code held steadily in the frame is accepted exactly once", () => {
    const gate = new ScanGate({ gapMs: 400, minRepeatMs: 700 });
    let accepted = 0;
    for (let t = 0; t <= 3000; t += 120) {
        if (gate.offer("7501234567890", t)) accepted += 1;
    }
    assert.equal(accepted, 1);
});

test("five deliberate re-entries of the same code produce five scans", () => {
    const gate = new ScanGate({ gapMs: 400, minRepeatMs: 700 });
    let accepted = 0;
    let t = 0;
    for (let round = 0; round < 5; round += 1) {
        // code visible for 480 ms (4 frames) ...
        for (let frame = 0; frame < 4; frame += 1, t += 120) {
            if (gate.offer("7501234567890", t)) accepted += 1;
        }
        // ... then removed from the frame for 600 ms
        t += 600;
    }
    assert.equal(accepted, 5);
});

test("same code seen again after a short gap but too soon is not re-accepted", () => {
    const gate = new ScanGate({ gapMs: 400, minRepeatMs: 700 });
    assert.equal(gate.offer("A", 0), true);
    assert.equal(gate.offer("A", 100), false);
    // gap of 450 ms satisfied, but only 550 ms since accept (< 700)
    assert.equal(gate.offer("A", 550), false);
    // now both conditions hold
    assert.equal(gate.offer("A", 1300), true);
});

test("different codes are always accepted", () => {
    const gate = new ScanGate();
    assert.equal(gate.offer("A", 0), true);
    assert.equal(gate.offer("B", 50), true);
    assert.equal(gate.offer("A", 100), true);
    assert.equal(gate.offer("", 150), false);
});

test("pending queue tracks order, retries, staleness and expiry", () => {
    const queue = new PendingQueue({ maxAttempts: 3 });
    queue.add("r1", "111", 0);
    queue.add("r2", "222", 100);
    assert.deepEqual(queue.ids(), ["r1", "r2"]);
    assert.equal(queue.size, 2);

    queue.markSent("r1", 5);
    assert.equal(queue.get("r1").status, "queued");
    assert.equal(queue.get("r1").attempts, 1);

    // attempts count uploads started; a server failure keeps the count.
    queue.markSent("r2", 6);
    assert.equal(queue.markSendFailed("r2", "server"), true); // 1 of 3
    queue.markSent("r2", 7);
    assert.equal(queue.markSendFailed("r2", "server"), true); // 2 of 3
    queue.markSent("r2", 8);
    assert.equal(queue.markSendFailed("r2", "server"), false); // 3 of 3 -> give up

    // only accepted scans can be stale; r2 was never accepted
    assert.deepEqual(queue.stale(3100, 3000), ["r1"]);
    assert.deepEqual(queue.stale(2500, 3000), []);

    const resolved = queue.resolve("r1", { status: "delivered" });
    assert.equal(resolved.barcode, "111");
    assert.equal(queue.size, 1);
    assert.equal(queue.resolve("missing"), null);

    const expired = queue.expire(40000, 30000);
    assert.equal(expired.length, 0, "an unaccepted scan is never expired");
    assert.equal(queue.size, 1);
});

test("a scan that could not be uploaded waits for the network without burning attempts", () => {
    const queue = new PendingQueue({ maxAttempts: 2 });
    queue.add("off1", "111", 0);
    queue.add("off2", "222", 10);
    for (let round = 0; round < 10; round += 1) {
        queue.markSent("off1", 100 + round);
        assert.equal(queue.markSendFailed("off1", "network"), true);
    }
    assert.equal(queue.get("off1").status, "offline");
    assert.equal(queue.get("off1").sentAt, null);
    assert.equal(queue.offlineCount, 1);
    assert.deepEqual(queue.unsentIds(), ["off1", "off2"]);
    assert.deepEqual(queue.queuedIds(), []);
    // a 30 s outage must not expire it: expiry only applies to accepted scans
    assert.equal(queue.expire(60000, 30000).length, 0);
    // once accepted it becomes a normal queued scan
    queue.markSent("off1", 60000);
    assert.deepEqual(queue.queuedIds(), ["off1"]);
    assert.deepEqual(queue.stale(63001, 3000), ["off1"]);
});

test("the queue survives a reload and in-flight scans are resent", () => {
    const queue = new PendingQueue();
    queue.add("a", "111", 0);
    queue.add("b", "222", 1);
    queue.markSent("a", 5);
    queue.markSent("b", 6);
    queue.markSendFailed("b", "network");
    const restored = PendingQueue.restore(queue.serialize());
    assert.deepEqual(restored.ids(), ["a", "b"]);
    assert.equal(restored.get("a").status, "queued");
    assert.equal(restored.get("b").status, "pending", "an offline scan is resent after a reload");
    assert.deepEqual(restored.unsentIds(), ["b"]);
    assert.equal(PendingQueue.restore("not json").size, 0);
    assert.equal(PendingQueue.restore(null).size, 0);
});

test("upload failures are classified so only real rejections are final", () => {
    assert.equal(classifySendError(new TypeError("Failed to fetch")), "network");
    assert.equal(classifySendError({ status: 0 }), "network");
    assert.equal(classifySendError({ status: 401 }), "session");
    assert.equal(classifySendError({ status: 409 }), "closed");
    assert.equal(classifySendError({ status: 400 }), "rejected");
    assert.equal(classifySendError({ status: 429 }), "rejected");
    assert.equal(classifySendError({ status: 500 }), "server");
    assert.equal(classifySendError({ status: 502 }), "server");
});

test("poll back-off grows to the cap and resets on results", () => {
    const backoff = new PollBackoff({ minMs: 300, maxMs: 1500, factor: 1.6 });
    assert.equal(backoff.next(false), 480);
    assert.equal(backoff.next(false), 768);
    assert.equal(backoff.next(false), 1229);
    assert.equal(backoff.next(false), 1500);
    assert.equal(backoff.next(false), 1500);
    assert.equal(backoff.next(true), 300);
});

test("scan retry delays are bounded", () => {
    assert.deepEqual([1, 2, 3, 4, 5].map(retryDelayMs), [400, 800, 1600, 3200, 4000]);
});

test("request ids are v4 UUIDs and unique", () => {
    const ids = new Set();
    for (let i = 0; i < 1000; i += 1) {
        const id = newRequestId();
        assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
        ids.add(id);
    }
    assert.equal(ids.size, 1000);
});

test("decoder crop covers the scan frame and downsizes large frames only", () => {
    const large = cropPlan(1920, 1080);
    assert.deepEqual(large, { sx: 192, sy: 324, sw: 1536, sh: 432, dw: 640, dh: 180 });
    const small = cropPlan(640, 480);
    assert.equal(small.dw, 512); // no upscaling beyond the crop itself
    assert.equal(small.dh, 192);
    assert.equal(cropPlan(0, 0), null);
});
