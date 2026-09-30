/** The sale being rung up. Plain data: stored as-is so a reload keeps it. */
export interface CartProduct {
  id: number;
  name: string;
  barcode: string | null;
  list_price: number;
  tax_ids: number[];
}

export interface CartLine {
  uuid: string;
  product: CartProduct;
  qty: number;
}

export interface Cart {
  /** Becomes the order's uuid: the key that makes resending harmless. */
  uuid: string;
  lines: CartLine[];
}

export const MAX_QTY = 9999;

export function emptyCart(newId: () => string): Cart {
  return { uuid: newId(), lines: [] };
}

/** One more of `product`: same product, same line. */
export function addProduct(cart: Cart, product: CartProduct, newId: () => string, qty = 1): Cart {
  const existing = cart.lines.find((line) => line.product.id === product.id);
  if (existing) return setQty(cart, existing.uuid, existing.qty + qty);
  const { id, name, barcode, list_price, tax_ids } = product;
  return { ...cart, lines: [...cart.lines, { uuid: newId(), product: { id, name, barcode, list_price, tax_ids }, qty }] };
}

/** Change a line's quantity; zero or less removes it. Whole units only. */
export function setQty(cart: Cart, lineUuid: string, qty: number): Cart {
  const units = Math.min(MAX_QTY, Math.floor(qty));
  if (!Number.isFinite(units) || units <= 0) return removeLine(cart, lineUuid);
  return { ...cart, lines: cart.lines.map((line) => (line.uuid === lineUuid ? { ...line, qty: units } : line)) };
}

export function removeLine(cart: Cart, lineUuid: string): Cart {
  return { ...cart, lines: cart.lines.filter((line) => line.uuid !== lineUuid) };
}

export function itemCount(cart: Cart): number {
  return cart.lines.reduce((count, line) => count + line.qty, 0);
}
