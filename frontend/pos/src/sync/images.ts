import type { ApiClient } from "../api/client";
import type { PosDb } from "../db/db";

/**
 * Product pictures for the grid: from the device first (they show offline),
 * else downloaded once with the register's token and kept. A picture is
 * replaced when the product's `image_version` changes. Downloads go a few at
 * a time so a long grid does not flood the connection.
 */
const CONCURRENCY = 4;
const urls = new Map<number, { version: string; url: string }>();
const waiting: (() => void)[] = [];
let running = 0;

async function slot<T>(task: () => Promise<T>): Promise<T> {
  if (running >= CONCURRENCY) await new Promise<void>((resolve) => waiting.push(resolve));
  running += 1;
  try {
    return await task();
  } finally {
    running -= 1;
    waiting.shift()?.();
  }
}

function remember(id: number, version: string, blob: Blob): string {
  const previous = urls.get(id);
  if (previous) URL.revokeObjectURL(previous.url);
  const url = URL.createObjectURL(blob);
  urls.set(id, { version, url });
  return url;
}

/** A displayable URL for the product's picture, or null (none, or offline and not kept). */
export async function productImageUrl(
  db: PosDb, client: ApiClient, product: { id: number; image_version: string | null }, online: boolean,
): Promise<string | null> {
  const version = product.image_version;
  if (!version) return null;
  const known = urls.get(product.id);
  if (known?.version === version) return known.url;
  const kept = await db.images.get(product.id);
  if (kept?.version === version) return remember(product.id, version, kept.blob);
  if (!online) return kept ? remember(product.id, kept.version, kept.blob) : null;
  try {
    const blob = await slot(() => client.productImage(product.id, 256));
    if (!blob) return null;
    await db.images.put({ id: product.id, version, blob });
    return remember(product.id, version, blob);
  } catch {
    // No picture is better than a broken register: an old one if kept.
    return kept ? remember(product.id, kept.version, kept.blob) : null;
  }
}

/** Forget the URLs of this session (on unpairing). */
export function dropImageUrls(): void {
  for (const { url } of urls.values()) URL.revokeObjectURL(url);
  urls.clear();
}
