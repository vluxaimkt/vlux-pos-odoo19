import type { Quote } from "../api/types";
import type { Cart } from "./cart";
import { round, sum } from "./money";

/** What the register knows about a sale tax (GET /catalog/taxes). */
export interface TaxInfo {
  id: number;
  name: string;
  amount: number;
  amount_type: string;
  price_include: boolean;
  children_tax_ids: number[];
  active: boolean;
}

export interface PricedLine {
  lineUuid: string;
  productId: number;
  name: string;
  qty: number;
  /** The unit price sent to the server (as the catalog holds it). */
  priceUnit: number;
  /** What one unit costs the customer, taxes included: what the ticket shows. */
  displayUnit: number;
  total: number;
  tax: number;
}

export interface Pricing {
  lines: PricedLine[];
  untaxed: number;
  tax: number;
  total: number;
  /** Computed by the server (quote) or, offline, locally. */
  source: "server" | "local";
  /**
   * Whether this total is guaranteed to be the one the server books, to the
   * cent. Only such a total may be charged; otherwise the sale waits for the
   * network (see localPricing).
   */
  exact: boolean;
}

/** The server's figures for the cart (POST /orders/quote). */
export function fromQuote(cart: Cart, quote: Quote): Pricing {
  const lines = cart.lines.map((line, index) => {
    const quoted = quote.lines[index];
    if (!quoted || quoted.product_id !== line.product.id) throw new Error("La cotización no corresponde al carrito.");
    return {
      lineUuid: line.uuid,
      productId: line.product.id,
      name: quoted.name,
      qty: quoted.qty,
      priceUnit: quoted.price_unit,
      displayUnit: round(quoted.price_subtotal_incl / quoted.qty),
      total: quoted.price_subtotal_incl,
      tax: round(quoted.price_subtotal_incl - quoted.price_subtotal),
    };
  });
  return {
    lines, untaxed: quote.amount_untaxed, tax: quote.amount_tax, total: quote.amount_total, source: "server", exact: true,
  };
}

/**
 * Price the cart without the network, from the local catalog.
 *
 * The total is exact only when every tax on every line is a percentage
 * already included in the price (IVA incluido, the Mexican retail norm) and
 * the register does not apply pricelists: then the total is the sum of
 * price × quantity, whatever the rounding method, and the server will book
 * the same amount. Anything else (taxes added on top, fixed or grouped taxes,
 * an unknown tax, pricelists) is marked inexact and must not be charged
 * offline. The tax split shown on an offline ticket is informative; the
 * server's is the one booked.
 */
export function localPricing(cart: Cart, taxes: Map<number, TaxInfo>, usesPricelist: boolean): Pricing {
  let exact = !usesPricelist;
  const lines = cart.lines.map((line) => {
    const lineTaxes = line.product.tax_ids.map((id) => taxes.get(id));
    const simple = lineTaxes.every(
      (tax) => tax && tax.price_include && tax.amount_type === "percent" && !tax.children_tax_ids.length,
    );
    if (!simple) exact = false;
    const total = round(line.product.list_price * line.qty);
    const rate = lineTaxes.reduce((acc, tax) => acc + (tax?.amount ?? 0), 0) / 100;
    const tax = simple && rate ? round(total - total / (1 + rate)) : 0;
    return {
      lineUuid: line.uuid, productId: line.product.id, name: line.product.name, qty: line.qty,
      priceUnit: line.product.list_price, displayUnit: round(total / line.qty), total, tax,
    };
  });
  const total = sum(lines.map((line) => line.total));
  const tax = sum(lines.map((line) => line.tax));
  return { lines, untaxed: round(total - tax), tax, total, source: "local", exact };
}
