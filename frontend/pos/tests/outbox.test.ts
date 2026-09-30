import { describe, expect, it } from "vitest";

import type { OrderRequest } from "../src/api/types";
import { backoff, enqueueSale, flushOutbox, retrySale } from "../src/sync/outbox";
import { fail, fakeClient, freshDb, ok } from "./helpers";

const sale = (uuid: string): OrderRequest => ({
  uuid, register_id: 1, lines: [{ product_id: 1, qty: 1 }], payments: [{ payment_method_id: 1, amount: 20 }],
});
const recorded = (uuid: string, duplicate = false) => ok({ id: 7, uuid, name: "Shop/0001", duplicate });

describe("sales queue", () => {
  it("sends pending sales oldest first and keeps the server's order", async () => {
    const db = freshDb();
    await enqueueSale(db, sale("b"), 2000);
    await enqueueSale(db, sale("a"), 1000);
    const { client, calls } = fakeClient((_m, _p, body) => recorded((body as OrderRequest).uuid));
    const result = await flushOutbox(client, db, 5000);
    expect(result).toMatchObject({ sent: 2, attention: 0, pending: 0 });
    expect(calls.map((c) => (c.body as OrderRequest).uuid)).toEqual(["a", "b"]);
    expect((await db.outbox.get("a"))?.result?.name).toBe("Shop/0001");
  });

  it("a sale the server already had counts as sent", async () => {
    const db = freshDb();
    await enqueueSale(db, sale("u"));
    const { client } = fakeClient(() => recorded("u", true));
    await flushOutbox(client, db);
    expect((await db.outbox.get("u"))?.status).toBe("sent");
  });

  it("offline: keeps the sale, backs off and stops sending the rest", async () => {
    const db = freshDb();
    await enqueueSale(db, sale("a"), 1000);
    await enqueueSale(db, sale("b"), 2000);
    const { client, calls } = fakeClient(() => new TypeError("Failed to fetch"));
    const result = await flushOutbox(client, db, 10_000);
    expect(result).toMatchObject({ sent: 0, pending: 1, offline: true });
    expect(calls).toHaveLength(1);
    const a = await db.outbox.get("a");
    expect(a).toMatchObject({ status: "pending", attempts: 1, nextAttemptAt: 10_000 + backoff(1) });

    // Not due yet: nothing is sent before the backoff.
    const again = fakeClient(() => recorded("a"));
    await flushOutbox(again.client, db, 10_001);
    expect(again.calls.map((c) => (c.body as OrderRequest).uuid)).toEqual(["b"]);
  });

  it("a refused sale is kept for attention, never dropped, and can be retried", async () => {
    const db = freshDb();
    await enqueueSale(db, sale("x"));
    const { client } = fakeClient(() => fail(409, "NO_OPEN_SESSION"));
    const result = await flushOutbox(client, db);
    expect(result.attention).toBe(1);
    expect(await db.outbox.get("x")).toMatchObject({ status: "attention", lastError: { code: "NO_OPEN_SESSION" } });

    await retrySale(db, "x");
    const later = fakeClient(() => recorded("x"));
    await flushOutbox(later.client, db);
    expect((await db.outbox.get("x"))?.status).toBe("sent");
  });

  it("server trouble is retried, not reported as the cashier's problem", async () => {
    const db = freshDb();
    await enqueueSale(db, sale("s"), 0);
    const { client } = fakeClient(() => fail(500, "INTERNAL_ERROR"));
    const result = await flushOutbox(client, db, 0);
    expect(result).toMatchObject({ pending: 1, attention: 0, offline: false });
    expect((await db.outbox.get("s"))?.status).toBe("pending");
  });

  it("the backoff grows and is capped at a minute", () => {
    expect(backoff(1)).toBe(2000);
    expect(backoff(3)).toBe(8000);
    expect(backoff(20)).toBe(60_000);
  });
});
