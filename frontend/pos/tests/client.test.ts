import { describe, expect, it } from "vitest";

import { ApiError, isRetryable, NetworkError } from "../src/api/client";
import { fail, fakeClient, ok } from "./helpers";

describe("ApiClient", () => {
  it("sends the bearer token and unwraps the envelope", async () => {
    const { client, calls } = fakeClient(() => ok({ api_version: "v1" }));
    const me = await client.me();
    expect(me.api_version).toBe("v1");
    expect(calls[0]?.headers.Authorization).toBe("Bearer tok");
  });

  it("turns a contract error into ApiError with its code and details", async () => {
    const { client } = fakeClient(() => fail(409, "PAYMENT_MISMATCH", { amount_due: 55.5 }));
    const error = await client.createOrder({ uuid: "u", register_id: 1, lines: [], payments: [] }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("PAYMENT_MISMATCH");
    expect(error.details).toEqual({ amount_due: 55.5 });
    expect(isRetryable(error)).toBe(false);
  });

  it("treats a dropped connection and a proxy page as network trouble", async () => {
    const down = fakeClient(() => new TypeError("Failed to fetch"));
    const dropped = await down.client.me().catch((e) => e);
    expect(dropped).toBeInstanceOf(NetworkError);
    expect(isRetryable(dropped)).toBe(true);

    const proxy = fakeClient(() => ({ status: 502, text: "<html>Bad gateway</html>" }));
    const bad = await proxy.client.me().catch((e) => e);
    expect(bad).toBeInstanceOf(NetworkError);
    expect(isRetryable(bad)).toBe(true);
  });

  it("retries server errors, rate limits and parallel-send conflicts only", () => {
    expect(isRetryable(new ApiError("INTERNAL_ERROR", "x", 500))).toBe(true);
    expect(isRetryable(new ApiError("RATE_LIMITED", "x", 429))).toBe(true);
    expect(isRetryable(new ApiError("CONFLICT", "x", 409, { retry: true }))).toBe(true);
    expect(isRetryable(new ApiError("CONFLICT", "x", 409))).toBe(false);
    expect(isRetryable(new ApiError("VALIDATION_ERROR", "x", 400))).toBe(false);
  });

  it("puts the cursor in the feed query", async () => {
    const { client, calls } = fakeClient(() => ok({ items: [], deleted: [], next_cursor: "c2", has_more: false }));
    await client.productsPage("c1", 200);
    expect(calls[0]?.path).toBe("/catalog/products?limit=200&cursor=c1");
  });
});
