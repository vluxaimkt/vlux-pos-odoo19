import { describe, expect, it } from "vitest";

import { ApiClient } from "../src/api/client";
import { productRow } from "../src/db/db";
import { browseProducts } from "../src/db/search";
import { productImageUrl } from "../src/sync/images";
import { freshDb, product } from "./helpers";

describe("product grid", () => {
  it("browses by POS category, sellable only, by name, within the register's categories", async () => {
    const db = freshDb();
    await db.products.bulkPut([
      product(1, "Refresco", { pos_category_ids: [10] }),
      product(2, "Agua", { pos_category_ids: [10] }),
      product(3, "Jamón", { pos_category_ids: [20] }),
      product(4, "Oculto", { pos_category_ids: [10], available_in_pos: false }),
    ].map(productRow));
    expect((await browseProducts(db, 10, null)).items.map((p) => p.name)).toEqual(["Agua", "Refresco"]);
    expect((await browseProducts(db, null, null)).items.map((p) => p.id)).toEqual([2, 3, 1]);
    expect((await browseProducts(db, null, [20])).items.map((p) => p.name)).toEqual(["Jamón"]);
    const page = await browseProducts(db, null, null, 2);
    expect(page.more).toBe(true);
  });

  it("downloads a picture once with the token, keeps it, and serves it offline", async () => {
    const db = freshDb();
    const calls: { url: string; auth: string }[] = [];
    const fetchImpl = (async (url: string, init: RequestInit) => {
      calls.push({ url, auth: (init.headers as Record<string, string>).Authorization ?? "" });
      if (url.includes("/products/2/")) return new Response("", { status: 404 });
      return new Response(new Uint8Array([137, 80, 78, 71]), { status: 200, headers: { "Content-Type": "image/png" } });
    }) as unknown as typeof fetch;
    const client = new ApiClient({ token: "tok", fetch: fetchImpl });

    const url = await productImageUrl(db, client, { id: 1, image_version: "v1" }, true);
    expect(url).toMatch(/^blob:/);
    expect(calls).toEqual([{ url: "/vlux/api/v1/catalog/products/1/image?size=256", auth: "Bearer tok" }]);
    expect((await db.images.get(1))?.version).toBe("v1");
    expect(await productImageUrl(db, client, { id: 1, image_version: "v1" }, false)).toBe(url);
    expect(calls).toHaveLength(1);

    expect(await productImageUrl(db, client, { id: 2, image_version: "v9" }, true)).toBeNull();
    expect(await productImageUrl(db, client, { id: 3, image_version: null }, true)).toBeNull();
    expect(await productImageUrl(db, client, { id: 4, image_version: "v1" }, false)).toBeNull();
  });
});
