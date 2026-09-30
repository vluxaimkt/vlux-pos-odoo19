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

export class PosDb extends Dexie {
  products!: Table<ProductRow, number>;
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
