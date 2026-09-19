// Run with: node --test vlux_mobile_scanner/static/tests/node/
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const core = require(path.join(__dirname, "..", "..", "src", "scanner", "scanner_core.js"));
const { ScanGate, PendingQueue, PollBackoff, retryDelayMs, newRequestId, cropPlan } = core;

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

    assert.equal(queue.markSendFailed("r2"), true); // attempt 1 -> retry allowed
    assert.equal(queue.markSendFailed("r2"), true); // attempt 2
    assert.equal(queue.markSendFailed("r2"), false); // attempt 3 -> give up

    assert.deepEqual(queue.stale(3100, 3000), ["r1", "r2"]);
    assert.deepEqual(queue.stale(2500, 3000), []);

    const resolved = queue.resolve("r1", { status: "delivered" });
    assert.equal(resolved.barcode, "111");
    assert.equal(queue.size, 1);
    assert.equal(queue.resolve("missing"), null);

    const expired = queue.expire(40000, 30000);
    assert.equal(expired.length, 1);
    assert.equal(queue.size, 0);
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
