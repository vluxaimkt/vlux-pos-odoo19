import { describe, expect, it } from "vitest";

import { getMeta } from "../src/db/db";
import { productByBarcode, searchProducts } from "../src/db/search";
import { syncFeed } from "../src/sync/catalog";
import { fail, fakeClient, freshDb, ok, product } from "./helpers";

const page = (items: unknown[], next: string, hasMore: boolean, deleted: number[] = []) =>
  ok({ items, deleted, next_cursor: next, has_more: hasMore, server_time: "" });

describe("catalog sync", () => {
  it("pages through the first sync and stores the cursor", async () => {
    const db = freshDb();
    const { client, calls } = fakeClient((_m, path) =>
      path.includes("cursor=c1")
        ? page([product(3, "Galletas")], "c2", false)
        : page([product(1, "Refresco"), product(2, "Café Molido")], "c1", true),
    );
    const progress = await syncFeed(client, db, "products");
    expect(progress).toMatchObject({ pages: 2, received: 3 });
    expect(await db.products.count()).toBe(3);
    expect(await getMeta(db, "cursor:products")).toBe("c2");
    expect(calls).toHaveLength(2);
  });

  it("applies changes and real deletions incrementally", async () => {
    const db = freshDb();
    await db.products.bulkPut([1, 2].map((id) => ({ ...product(id, `P${id}`), words: [] })));
    await db.meta.put({ key: "cursor:products", value: "c9" });
    const { client, calls } = fakeClient(() => page([product(1, "Renombrado")], "c10", false, [2]));
    await syncFeed(client, db, "products");
    expect(calls[0]?.path).toContain("cursor=c9");
    expect((await db.products.get(1))?.name).toBe("Renombrado");
    expect(await db.products.get(2)).toBeUndefined();
  });

  it("starts over once when the cursor is too old", async () => {
    const db = freshDb();
    await db.products.put({ ...product(9, "Viejo"), words: [] });
    await db.meta.put({ key: "cursor:products", value: "ancient" });
    const { client } = fakeClient((_m, path) =>
      path.includes("ancient") ? fail(409, "RESYNC_REQUIRED") : page([product(1, "Nuevo")], "c1", false),
    );
    await syncFeed(client, db, "products");
    expect(await db.products.toCollection().primaryKeys()).toEqual([1]);
  });

  it("does not loop when the server keeps refusing", async () => {
    const db = freshDb();
    await db.meta.put({ key: "cursor:products", value: "x" });
    const { client } = fakeClient(() => fail(409, "RESYNC_REQUIRED"));
    await expect(syncFeed(client, db, "products")).rejects.toMatchObject({ code: "RESYNC_REQUIRED" });
  });
});

describe("local search", () => {
  it("finds by words without accents and by barcode, sellable only", async () => {
    const db = freshDb();
    const { client } = fakeClient(() =>
      page([
        product(1, "Café Molido 500 g", { barcode: "7501000000011" }),
        product(2, "Cafetera", { available_in_pos: false }),
        product(3, "Leche entera", { active: false, barcode: "7501000000028" }),
        product(4, "Galletas de café"),
      ], "c", false),
    );
    await syncFeed(client, db, "products");

    expect((await searchProducts(db, "cafe")).map((p) => p.id)).toEqual([1, 4]);
    expect((await searchProducts(db, "cafe mol")).map((p) => p.id)).toEqual([1]);
    expect((await productByBarcode(db, "7501000000011"))?.id).toBe(1);
    expect(await productByBarcode(db, "7501000000028")).toBeUndefined();
  });
});
