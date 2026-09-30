import { describe, expect, it } from "vitest";

import type { Quote } from "../src/api/types";
import { addProduct, type CartProduct, emptyCart, itemCount, removeLine, setQty } from "../src/sale/cart";
import { round } from "../src/sale/money";
import { buildOrder } from "../src/sale/order";
import { cashSuggestions, paymentState } from "../src/sale/payment";
import { fromQuote, localPricing, type TaxInfo } from "../src/sale/pricing";

let counter = 0;
const newId = () => `id-${++counter}`;
const soda: CartProduct = { id: 1, name: "Refresco", barcode: "750", list_price: 20, tax_ids: [16] };
const cookies: CartProduct = { id: 2, name: "Galletas", barcode: null, list_price: 15.5, tax_ids: [] };
const iva = (id: number, extra: Partial<TaxInfo> = {}): TaxInfo => ({
  id, name: "IVA 16%", amount: 16, amount_type: "percent", price_include: true, children_tax_ids: [], active: true, ...extra,
});
const cash = { id: 10, name: "Efectivo", is_cash: true };
const card = { id: 11, name: "Tarjeta", is_cash: false };

describe("cart", () => {
  it("adds, merges the same product, changes and removes lines", () => {
    let cart = emptyCart(newId);
    cart = addProduct(cart, soda, newId);
    cart = addProduct(cart, soda, newId);
    cart = addProduct(cart, cookies, newId);
    expect(cart.lines.map((l) => [l.product.id, l.qty])).toEqual([[1, 2], [2, 1]]);
    expect(itemCount(cart)).toBe(3);

    const sodaLine = cart.lines[0]!.uuid;
    cart = setQty(cart, sodaLine, 5.7);
    expect(cart.lines[0]!.qty).toBe(5);
    cart = setQty(cart, sodaLine, 0);
    expect(cart.lines.map((l) => l.product.id)).toEqual([2]);
    cart = removeLine(cart, cart.lines[0]!.uuid);
    expect(cart.lines).toEqual([]);
  });
});

describe("pricing", () => {
  const taxes = new Map([[16, iva(16)]]);

  it("prices included taxes exactly offline, same as the server's quote", () => {
    let cart = emptyCart(newId);
    cart = addProduct(cart, soda, newId, 2);
    cart = addProduct(cart, cookies, newId);
    const local = localPricing(cart, taxes, false);
    // The figures the server quoted for this very cart (vlux_pos_api tests).
    expect(local).toMatchObject({ total: 55.5, tax: 5.52, untaxed: 49.98, exact: true, source: "local" });
  });

  it("refuses to call a total exact when the server could book another", () => {
    const cart = addProduct(emptyCart(newId), soda, newId);
    expect(localPricing(cart, new Map([[16, iva(16, { price_include: false })]]), false).exact).toBe(false);
    expect(localPricing(cart, new Map([[16, iva(16, { amount_type: "fixed" })]]), false).exact).toBe(false);
    expect(localPricing(cart, new Map(), false).exact).toBe(false); // unknown tax
    expect(localPricing(cart, taxes, true).exact).toBe(false); // pricelists
  });

  it("uses the server's quote as is", () => {
    const cart = addProduct(emptyCart(newId), soda, newId, 2);
    const quote: Quote = {
      pricelist_id: 1, amount_untaxed: 34.48, amount_tax: 5.52, amount_total: 40,
      lines: [{ uuid: null, product_id: 1, name: "Refresco 600 ml", qty: 2, price_unit: 20, catalog_price: 20,
        price_overridden: false, tax_ids: [16], price_subtotal: 34.48, price_subtotal_incl: 40 }],
    };
    expect(fromQuote(cart, quote)).toMatchObject({ total: 40, tax: 5.52, source: "server", exact: true });
    expect(() => fromQuote(addProduct(cart, cookies, newId), quote)).toThrow();
  });

  it("rounds half up like the server", () => {
    expect(round(1.005)).toBe(1.01);
    expect(round(2.675)).toBe(2.68);
    expect(round(0.1 + 0.2)).toBe(0.3);
  });
});

describe("payment", () => {
  it("gives change only from cash", () => {
    expect(paymentState(55.5, [{ method: cash, amount: 100 }])).toMatchObject({ complete: true, change: 44.5 });
    expect(paymentState(55.5, [{ method: cash, amount: 50 }])).toMatchObject({ complete: false, remaining: 5.5 });
    expect(paymentState(55.5, [{ method: card, amount: 60 }])).toMatchObject({ complete: false });
    expect(paymentState(55.5, [{ method: card, amount: 30 }, { method: cash, amount: 30 }]))
      .toMatchObject({ complete: true, change: 4.5 });
    expect(paymentState(55.5, [{ method: card, amount: 55.5 }])).toMatchObject({ complete: true, change: 0 });
  });

  it("suggests the exact amount and the next bills", () => {
    expect(cashSuggestions(55.5)).toEqual([55.5, 60, 100, 200, 500]);
    expect(cashSuggestions(0)).toEqual([]);
  });
});

describe("order", () => {
  it("sends the charged prices, the payments and the expected total", () => {
    let cart = emptyCart(() => "order-uuid");
    cart = addProduct(cart, soda, () => "line-1", 2);
    const pricing = localPricing(cart, new Map([[16, iva(16)]]), false);
    const order = buildOrder(cart, pricing, [{ method: cash, amount: 50 }], {
      registerId: 3, sessionId: 7, employeeId: 9, now: new Date("2026-09-30T12:00:00Z"),
    });
    expect(order).toEqual({
      uuid: "order-uuid", register_id: 3, session_id: 7, employee_id: 9,
      lines: [{ uuid: "line-1", product_id: 1, qty: 2, price_unit: 20 }],
      payments: [{ payment_method_id: 10, amount: 50 }],
      expected_total: 40, created_at: "2026-09-30T12:00:00.000Z",
    });
  });

  it("never builds an order from an inexact total", () => {
    const cart = addProduct(emptyCart(newId), soda, newId);
    const pricing = localPricing(cart, new Map(), false);
    expect(() => buildOrder(cart, pricing, [], { registerId: 1, sessionId: 1, employeeId: null, now: new Date() }))
      .toThrow();
  });
});
