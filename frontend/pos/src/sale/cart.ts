/** The sale being rung up. Plain data: stored as-is so a reload keeps it. */
export interface CartProduct {
  id: number;
  name: string;
  barcode: string | null;
  list_price: number;
  tax_ids: number[];
  /** Sold by weight: kilos with up to three decimals. */
  to_weight?: boolean;
  /** Unit of measure (kg, g…), kept for products sold by weight. */
  uom?: { name: string };
}

export interface CartLine {
  uuid: string;
  product: CartProduct;
  qty: number;
  /** A price read from a scale label or typed as wholesale; otherwise the catalog price applies. */
  priceUnit?: number;
  priceFromBarcode?: boolean;
  /** Sold at a wholesale price the cashier typed for this customer (it varies between customers). */
  wholesale?: boolean;
}

export interface CartCustomer {
  id: number;
  name: string;
}

export interface Cart {
  /** Becomes the order's uuid: the key that makes resending harmless. */
  uuid: string;
  lines: CartLine[];
  /** Needed to sell on credit; optional otherwise. */
  customer?: CartCustomer | null;
}

export const MAX_QTY = 9999;
/** Grams: the precision of a scale and of a weight label. */
export const WEIGHT_DECIMALS = 3;

export interface AddOptions {
  /** The price printed on a scale label. */
  priceUnit?: number;
}

/** What one unit of the line costs (taxes as the catalog holds them). */
export function unitPrice(line: CartLine): number {
  return line.priceUnit ?? line.product.list_price;
}

/** Kilos of a product sold by weight (a price label instead sells one labelled package). */
export function isWeighed(line: CartLine): boolean {
  return Boolean(line.product.to_weight) && !line.priceFromBarcode;
}

/** "0.375 kg" for weighed lines, "2" for pieces. */
export function qtyLabel(qty: number, unit?: string): string {
  return unit ? `${qty.toFixed(WEIGHT_DECIMALS)} ${unit}` : String(qty);
}

export function emptyCart(newId: () => string): Cart {
  return { uuid: newId(), lines: [] };
}

/**
 * Add `qty` of `product`. Pieces of the same product share a line; each
 * weighing or scale label is its own line (as on the scale's ticket).
 */
export function addProduct(cart: Cart, product: CartProduct, newId: () => string, qty = 1, options: AddOptions = {}): Cart {
  const separate = Boolean(product.to_weight) || options.priceUnit !== undefined;
  const existing = separate ? undefined : cart.lines.find((line) => line.product.id === product.id && line.priceUnit === undefined);
  if (existing) return setQty(cart, existing.uuid, existing.qty + qty);
  const { id, name, barcode, list_price, tax_ids, to_weight, uom } = product;
  const line: CartLine = {
    uuid: newId(),
    product: { id, name, barcode, list_price, tax_ids, ...(to_weight ? { to_weight: true, uom: { name: uom?.name ?? "kg" } } : {}) },
    qty: 0,
    ...(options.priceUnit !== undefined ? { priceUnit: options.priceUnit, priceFromBarcode: true } : {}),
  };
  const quantity = cleanQty(line, qty);
  if (quantity <= 0) return cart;
  return { ...cart, lines: [...cart.lines, { ...line, qty: quantity }] };
}

/** Whole pieces, or kilos to the gram for products sold by weight; 0 when invalid. */
function cleanQty(line: CartLine, qty: number): number {
  if (!Number.isFinite(qty)) return 0;
  const factor = 10 ** WEIGHT_DECIMALS;
  const value = isWeighed(line) ? Math.round(qty * factor) / factor : Math.floor(qty);
  return Math.min(MAX_QTY, Math.max(0, value));
}

/** Change a line's quantity; zero or less removes it. */
export function setQty(cart: Cart, lineUuid: string, qty: number): Cart {
  const line = cart.lines.find((candidate) => candidate.uuid === lineUuid);
  if (!line) return cart;
  const quantity = cleanQty(line, qty);
  if (quantity <= 0) return removeLine(cart, lineUuid);
  return { ...cart, lines: cart.lines.map((candidate) => (candidate.uuid === lineUuid ? { ...candidate, qty: quantity } : candidate)) };
}

/**
 * Sell a line at a wholesale price per piece (or per kilo) typed by the
 * cashier; `null` goes back to the catalog price. A price read from a scale
 * label is not wholesale and stays as it is.
 */
export function setWholesalePrice(cart: Cart, lineUuid: string, price: number | null): Cart {
  return {
    ...cart,
    lines: cart.lines.map((line) => {
      if (line.uuid !== lineUuid || line.priceFromBarcode) return line;
      if (price === null) {
        const { priceUnit: _price, wholesale: _wholesale, ...rest } = line;
        return rest;
      }
      if (!Number.isFinite(price) || price <= 0) return line;
      return { ...line, priceUnit: Math.round(price * 100) / 100, wholesale: true };
    }),
  };
}

export function removeLine(cart: Cart, lineUuid: string): Cart {
  return { ...cart, lines: cart.lines.filter((line) => line.uuid !== lineUuid) };
}

/**
 * A product was edited (price, name, taxes…): lines of it in the cart take
 * the new data, except a price read from a scale label, which stays.
 */
export function refreshProduct(cart: Cart, product: CartProduct): Cart {
  if (!cart.lines.some((line) => line.product.id === product.id)) return cart;
  const { id, name, barcode, list_price, tax_ids, to_weight, uom } = product;
  return {
    ...cart,
    lines: cart.lines.map((line) => line.product.id !== id ? line : {
      ...line,
      product: { id, name, barcode, list_price, tax_ids, ...(to_weight ? { to_weight: true, uom: { name: uom?.name ?? "kg" } } : {}) },
    }),
  };
}

export function setCustomer(cart: Cart, customer: CartCustomer | null): Cart {
  return { ...cart, customer };
}

/** Articles in the sale: a weighed line counts as one. */
export function itemCount(cart: Cart): number {
  return cart.lines.reduce((count, line) => count + (isWeighed(line) ? 1 : line.qty), 0);
}
