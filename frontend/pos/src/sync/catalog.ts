import { ApiError, type ApiClient } from "../api/client";
import type { FeedPage } from "../api/types";
import { customerRow, type PosDb, productRow } from "../db/db";

export type FeedKind = "products" | "customers";

export interface SyncProgress {
  kind: FeedKind;
  pages: number;
  received: number;
  deleted: number;
}

/**
 * Bring the local copy of a feed up to date (docs/API_V1.md §4.1).
 *
 * Each page is applied in one transaction together with its cursor: items,
 * then deletions, then the cursor. A crash between pages resumes from the
 * last applied page; a retried page changes nothing. When the server says the
 * cursor is too old (RESYNC_REQUIRED) or unknown, the table is emptied and
 * the feed starts over, once.
 */
export async function syncFeed(
  client: ApiClient,
  db: PosDb,
  kind: FeedKind,
  onProgress?: (progress: SyncProgress) => void,
): Promise<SyncProgress> {
  const key = `cursor:${kind}`;
  const table = kind === "products" ? db.products : db.customers;
  const progress: SyncProgress = { kind, pages: 0, received: 0, deleted: 0 };
  let cursor = (await db.meta.get(key))?.value as string | undefined;
  let restarted = false;

  for (;;) {
    let page: FeedPage<unknown>;
    try {
      page = kind === "products" ? await client.productsPage(cursor) : await client.customersPage(cursor);
    } catch (error) {
      const stale = error instanceof ApiError && (error.code === "RESYNC_REQUIRED" || error.code === "INVALID_CURSOR");
      if (!stale || restarted) throw error;
      restarted = true;
      await db.transaction("rw", table, db.meta, async () => {
        await table.clear();
        await db.meta.delete(key);
      });
      cursor = undefined;
      continue;
    }

    await db.transaction("rw", table, db.meta, async () => {
      if (kind === "products") {
        await db.products.bulkPut((page as FeedPage<Parameters<typeof productRow>[0]>).items.map(productRow));
      } else {
        await db.customers.bulkPut((page as FeedPage<Parameters<typeof customerRow>[0]>).items.map(customerRow));
      }
      if (page.deleted.length) await table.bulkDelete(page.deleted);
      await db.meta.put({ key, value: page.next_cursor });
    });
    cursor = page.next_cursor;
    progress.pages += 1;
    progress.received += page.items.length;
    progress.deleted += page.deleted.length;
    onProgress?.({ ...progress });
    if (!page.has_more) return progress;
  }
}
