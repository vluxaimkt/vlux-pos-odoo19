import { describe, expect, it } from "vitest";

import { addProduct, type CartProduct, emptyCart, itemCount, qtyLabel, setQty } from "../src/sale/cart";
import { buildOrder } from "../src/sale/order";
import { fromQuote, localPricing, type TaxInfo } from "../src/sale/pricing";

let counter = 0;
const newId = () => `w-${++counter}`;
const ham: CartProduct = { id: 7, name: "Jamón", barcode: "2100007000000", list_price: 17.4, tax_ids: [1], to_weight: true, uom: { name: "kg" } };
const soda: CartProduct = { id: 1, name: "Refresco", barcode: "750", list_price: 20, tax_ids: [1] };
// Food: IVA 0 %, included (a percent tax that changes nothing keeps the arithmetic plain).
const taxes = new Map<number, TaxInfo>([[1, {
  id: 1, name: "IVA 0%", amount: 0, amount_type: "percent", price_include: true, children_tax_ids: [], active: true,
}]]);

describe("products sold by weight", () => {
  it("keeps kilos to the gram and gives each weighing its own line", () => {
    let cart = addProduct(emptyCart(newId), ham, newId, 0.3754);
    cart = addProduct(cart, ham, newId, 0.125);
    expect(cart.lines.map((line) => line.qty)).toEqual([0.375, 0.125]);
    expect(itemCount(cart)).toBe(2);
    cart = setQty(cart, cart.lines[0]!.uuid, 1.2);
    expect(cart.lines[0]!.qty).toBe(1.2);
    expect(qtyLabel(0.375, "kg")).toBe("0.375 kg");
    // Pieces stay whole.
    cart = addProduct(cart, soda, newId, 2.7);
    expect(cart.lines[2]!.qty).toBe(2);
  });

  it("adds up like the company rounds taxes", () => {
    let cart = addProduct(emptyCart(newId), ham, newId, 0.375);
    cart = addProduct(cart, ham, newId, 0.125);
    // 6.525 → 6.53 and 2.175 → 2.18 per line; 8.70 when the sum is rounded once (same as the server test).
    expect(localPricing(cart, taxes, false, "round_per_line").total).toBe(8.71);
    expect(localPricing(cart, taxes, false, "round_globally").total).toBe(8.7);
    expect(localPricing(cart, taxes, false).lines[0]).toMatchObject({ unit: "kg", displayUnit: 17.4, total: 6.53 });
  });

  it("a scale price label is one package at the label's price, sent as such", () => {
    const cart = addProduct(emptyCart(newId), ham, newId, 1, { priceUnit: 37.5 });
    const line = cart.lines[0]!;
    expect(line).toMatchObject({ qty: 1, priceUnit: 37.5, priceFromBarcode: true });
    const pricing = localPricing(cart, taxes, false);
    expect(pricing.total).toBe(37.5);
    expect(pricing.lines[0]!.unit).toBeUndefined();
    const order = buildOrder(cart, pricing, [], { registerId: 1, sessionId: null, employeeId: null, now: new Date(0) });
    expect(order.lines[0]).toMatchObject({ price_unit: 37.5, price_from_barcode: true });
  });

  it("the ticket shows the price per kilo from the server's quote", () => {
    const cart = addProduct(emptyCart(newId), ham, newId, 0.375);
    const pricing = fromQuote(cart, {
      lines: [{ uuid: null, product_id: 7, name: "Jamón", qty: 0.375, price_unit: 17.4, catalog_price: 17.4, price_overridden: false,
        tax_ids: [1], price_subtotal: 6.53, price_subtotal_incl: 6.53 }],
      amount_untaxed: 6.53, amount_tax: 0, amount_total: 6.53,
    } as never);
    expect(pricing.lines[0]).toMatchObject({ displayUnit: 17.4, unit: "kg", total: 6.53 });
  });
});

describe("editing a product from the register", () => {
  it("updates its cart lines but keeps a scale-label price", async () => {
    const { refreshProduct } = await import("../src/sale/cart");
    let cart = addProduct(emptyCart(newId), soda, newId, 2);
    cart = addProduct(cart, ham, newId, 1, { priceUnit: 37.5 });
    cart = refreshProduct(cart, { ...soda, list_price: 22, name: "Refresco 600" });
    cart = refreshProduct(cart, { ...ham, list_price: 99 });
    expect(cart.lines[0]!.product).toMatchObject({ list_price: 22, name: "Refresco 600" });
    expect(cart.lines[0]!.qty).toBe(2);
    expect(cart.lines[1]!.priceUnit).toBe(37.5);
  });
});
