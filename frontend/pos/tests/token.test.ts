import { describe, expect, it } from "vitest";

import { getMeta, setMeta } from "../src/db/db";
import { META_TOKEN_INFO, needsRenewal, renewIfDue } from "../src/sync/token";
import { fakeClient, freshDb, ok } from "./helpers";

const DAY = 24 * 60 * 60_000;
const at = (ms: number) => new Date(ms).toISOString();

describe("token renewal", () => {
  it("renews renewable tokens with less than 15 days left, only those", () => {
    expect(needsRenewal({ renewable: true, expires_at: at(20 * DAY) }, 0)).toBe(false);
    expect(needsRenewal({ renewable: true, expires_at: at(10 * DAY) }, 0)).toBe(true);
    expect(needsRenewal({ renewable: false, expires_at: at(1 * DAY) }, 0)).toBe(false);
    expect(needsRenewal({ renewable: true, expires_at: null }, 0)).toBe(false);
    expect(needsRenewal(undefined, 0)).toBe(false);
  });

  it("stores the new token and its details together", async () => {
    const db = freshDb();
    await setMeta(db, "token", "old-token-value");
    await setMeta(db, META_TOKEN_INFO, { renewable: true, expires_at: at(5 * DAY), register_id: 1 });
    const { client, calls } = fakeClient(() =>
      ok({ token: "new-token-value", renewable: true, expires_at: at(35 * DAY), register_id: 1, scopes: [] }),
    );

    expect(await renewIfDue(client, db, 0)).toBe("new-token-value");
    expect(calls[0]).toMatchObject({ method: "POST", path: "/token/rotate" });
    expect(await getMeta(db, "token")).toBe("new-token-value");
    expect(await getMeta(db, META_TOKEN_INFO)).toMatchObject({ expires_at: at(35 * DAY) });
    expect(JSON.stringify(await getMeta(db, META_TOKEN_INFO))).not.toContain("new-token-value");
  });

  it("does nothing while the token is fresh, and keeps the old one if the call fails", async () => {
    const db = freshDb();
    await setMeta(db, "token", "old-token-value");
    await setMeta(db, META_TOKEN_INFO, { renewable: true, expires_at: at(25 * DAY) });
    const idle = fakeClient(() => ok({}));
    expect(await renewIfDue(idle.client, db, 0)).toBeNull();
    expect(idle.calls).toHaveLength(0);

    await setMeta(db, META_TOKEN_INFO, { renewable: true, expires_at: at(1 * DAY) });
    const down = fakeClient(() => new TypeError("Failed to fetch"));
    await expect(renewIfDue(down.client, db, 0)).rejects.toThrow();
    expect(await getMeta(db, "token")).toBe("old-token-value");
  });
});
