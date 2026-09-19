/*
 * VLUX Mobile Scanner - pure client logic (no DOM, no network).
 *
 * Loaded by the scanner page as a plain script (window.VluxScannerCore) and by
 * the Node test runner (module.exports), so the queue / debounce / back-off
 * behaviour is unit tested without a browser or a camera.
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.VluxScannerCore = api;
    }
})(typeof window !== "undefined" ? window : null, function () {
    "use strict";

    /**
     * Decides whether a camera detection is a NEW scan or the same code that is
     * simply still inside the frame.
     *
     * Rules:
     *  - a different code than the last accepted one is always accepted;
     *  - the same code is accepted again only if it LEFT the frame for at
     *    least `gapMs` (no detection of that code during that window) and at
     *    least `minRepeatMs` passed since it was last accepted;
     *  - every detection refreshes "last seen", so a code held steadily in
     *    front of the camera is accepted exactly once.
     *
     * Manual entries and the "repeat" button bypass the gate on purpose.
     */
    class ScanGate {
        constructor(options = {}) {
            this.gapMs = options.gapMs ?? 400;
            this.minRepeatMs = options.minRepeatMs ?? 700;
            this.reset();
        }

        reset() {
            this.lastCode = "";
            this.lastAcceptedAt = -Infinity;
            this.lastSeenAt = -Infinity;
        }

        /** Register a detection; returns true when it should be sent as a scan. */
        offer(code, now) {
            code = String(code || "").trim();
            if (!code) {
                return false;
            }
            let accept;
            if (code !== this.lastCode) {
                accept = true;
            } else {
                const leftFrame = now - this.lastSeenAt >= this.gapMs;
                const spaced = now - this.lastAcceptedAt >= this.minRepeatMs;
                accept = leftFrame && spaced;
            }
            if (accept) {
                this.lastCode = code;
                this.lastAcceptedAt = now;
            }
            if (code === this.lastCode) {
                this.lastSeenAt = now;
            }
            return accept;
        }
    }

    /**
     * Local queue of scans awaiting a POS result.
     * Keeps insertion order so the UI can show "3 pending" and the history in
     * the order the cashier scanned.
     */
    class PendingQueue {
        constructor(options = {}) {
            this.maxAttempts = options.maxAttempts ?? 4;
            this.items = new Map();
        }

        add(requestId, barcode, now) {
            this.items.set(requestId, {
                requestId,
                barcode,
                createdAt: now,
                sentAt: null,
                attempts: 0,
                status: "pending",
            });
            return this.items.get(requestId);
        }

        get size() {
            return this.items.size;
        }

        ids() {
            return Array.from(this.items.keys());
        }

        get(requestId) {
            return this.items.get(requestId) || null;
        }

        markSent(requestId, now) {
            const item = this.items.get(requestId);
            if (item) {
                item.sentAt = now;
                item.attempts += 1;
                item.status = "queued";
            }
            return item;
        }

        /** Record a failed send; returns true when another attempt is allowed. */
        markSendFailed(requestId) {
            const item = this.items.get(requestId);
            if (!item) {
                return false;
            }
            item.attempts += 1;
            item.status = "retry";
            return item.attempts < this.maxAttempts;
        }

        resolve(requestId, result) {
            const item = this.items.get(requestId);
            if (!item) {
                return null;
            }
            this.items.delete(requestId);
            return { ...item, result };
        }

        /** Ids waiting longer than `olderThanMs` (used for the push safety net). */
        stale(now, olderThanMs) {
            return this.ids().filter((id) => now - this.items.get(id).createdAt >= olderThanMs);
        }

        expire(now, ttlMs) {
            const expired = [];
            for (const [id, item] of this.items) {
                if (now - item.createdAt >= ttlMs) {
                    this.items.delete(id);
                    expired.push(item);
                }
            }
            return expired;
        }
    }

    /**
     * Polling cadence for the batch results endpoint. Starts fast right after
     * a scan, backs off while nothing arrives, resets when a result lands and
     * stops entirely when the queue is empty.
     */
    class PollBackoff {
        constructor(options = {}) {
            this.minMs = options.minMs ?? 300;
            this.maxMs = options.maxMs ?? 1500;
            this.factor = options.factor ?? 1.6;
            this.current = this.minMs;
        }

        reset() {
            this.current = this.minMs;
            return this.current;
        }

        next(gotResult) {
            if (gotResult) {
                return this.reset();
            }
            this.current = Math.min(this.maxMs, Math.round(this.current * this.factor));
            return this.current;
        }
    }

    /**
     * Retry delay for re-sending a scan whose HTTP request failed.
     * Idempotent on the server (same request_id), so retries are safe.
     */
    function retryDelayMs(attempt) {
        return Math.min(4000, 400 * Math.pow(2, Math.max(0, attempt - 1)));
    }

    function newRequestId() {
        if (typeof crypto !== "undefined" && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        const bytes = new Uint8Array(16);
        if (typeof crypto !== "undefined" && crypto.getRandomValues) {
            crypto.getRandomValues(bytes);
        } else {
            for (let i = 0; i < 16; i += 1) {
                bytes[i] = Math.floor(Math.random() * 256);
            }
        }
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    /**
     * Region of the video frame handed to the decoder: the visible scan frame
     * (centre band), downscaled so its width never exceeds `maxWidth` pixels.
     * Returns source rect + destination size for drawImage.
     */
    function cropPlan(videoWidth, videoHeight, options = {}) {
        const widthRatio = options.widthRatio ?? 0.8;
        const heightRatio = options.heightRatio ?? 0.4;
        const maxWidth = options.maxWidth ?? 640;
        if (!videoWidth || !videoHeight) {
            return null;
        }
        const sw = Math.round(videoWidth * widthRatio);
        const sh = Math.round(videoHeight * heightRatio);
        const sx = Math.round((videoWidth - sw) / 2);
        const sy = Math.round((videoHeight - sh) / 2);
        const scale = Math.min(1, maxWidth / sw);
        return {
            sx,
            sy,
            sw,
            sh,
            dw: Math.max(1, Math.round(sw * scale)),
            dh: Math.max(1, Math.round(sh * scale)),
        };
    }

    return { ScanGate, PendingQueue, PollBackoff, retryDelayMs, newRequestId, cropPlan };
});
