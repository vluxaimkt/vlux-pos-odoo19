/**
 * Barcode sources: anything that reads codes for the register. The sale
 * only knows `ScanHandler`; a new kind of reader (a linked phone, a camera,
 * a serial scanner…) is a new source, without touching the sale.
 */

/** What became of a read, so the device that made it can show it. */
export interface ScanOutcome {
  status: "delivered" | "not_found" | "failed";
  /** Machine code: ADDED_TO_CART, WEIGHT_REQUIRED, PRODUCT_NOT_FOUND… */
  code: string;
  message: string;
  product?: { id: number; name: string; price: number };
}

export type ScanHandler = (barcode: string) => Promise<ScanOutcome>;

export interface ScanSource {
  /** Start delivering reads to `handler`; returns how to stop. */
  start(handler: ScanHandler): () => void;
}
