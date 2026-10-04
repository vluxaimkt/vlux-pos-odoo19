import Dexie, { type Table } from "dexie";

import type { Customer, OrderRequest, OrderResult, Product } from "../api/types";
import { normalize, searchWords } from "../lib/text";

export interface ProductRow extends Product {
  /** Normalized words of name, code and barcode (multi-entry index). */
  words: string[];
}

export interface MetaRow {
  key: string;
  value: unknown;
}

export type OutboxStatus = "pending" | "sent" | "attention";

/** A sale waiting to reach the server, or already there. Never deleted by the app. */
export interface OutboxRow {
  uuid: string;
  body: OrderRequest;
  status: OutboxStatus;
  attempts: number;
  createdAt: string;
  nextAttemptAt: number;
  lastError?: { code: string; message: string; details?: Record<string, unknown> };
  result?: OrderResult;
}

/** A product picture kept on the device, so the grid shows it offline. */
export interface ImageRow {
  /** Product id. */
  id: number;
  /** The feed's image_version it belongs to: a new picture replaces it. */
  version: string;
  blob: Blob;
}

export class PosDb extends Dexie {
  products!: Table<ProductRow, number>;
  images!: Table<ImageRow, number>;
  customers!: Table<Customer & { search: string }, number>;
  meta!: Table<MetaRow, string>;
  outbox!: Table<OutboxRow, string>;

  constructor(name = "vlux-pos") {
    super(name);
    this.version(1).stores({
      products: "id, barcode, default_code, *words",
      customers: "id, barcode, search",
      meta: "key",
      outbox: "uuid, status, createdAt, nextAttemptAt",
    });
    // v2: products carry `to_weight` and `description` and are browsed by POS
    // category; pictures are kept. Fetch the catalog again so every row has them.
    this.version(2).stores({
      products: "id, barcode, default_code, *words, *pos_category_ids",
      images: "id",
    }).upgrade(async (tx) => {
      await tx.table("products").clear();
      await tx.table("meta").delete("cursor:products");
    });
  }
}

export function productRow(product: Product): ProductRow {
  return { ...product, words: searchWords(product.name, product.default_code, product.barcode) };
}

export function customerRow(customer: Customer): Customer & { search: string } {
  return { ...customer, search: normalize(customer.name) };
}

export async function getMeta<T>(db: PosDb, key: string): Promise<T | undefined> {
  return (await db.meta.get(key))?.value as T | undefined;
}

export async function setMeta(db: PosDb, key: string, value: unknown): Promise<void> {
  await db.meta.put({ key, value });
}
