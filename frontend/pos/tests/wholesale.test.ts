import { describe, expect, it } from "vitest";

import { addProduct, type CartProduct, emptyCart, setWholesalePrice, unitPrice } from "../src/sale/cart";
import { buildOrder } from "../src/sale/order";
import { localPricing, type TaxInfo } from "../src/sale/pricing";

let counter = 0;
const newId = () => `w-${++counter}`;
const soda: CartProduct = { id: 1, name: "Refresco", barcode: "750", list_price: 20, tax_ids: [16] };
const iva: TaxInfo = { id: 16, name: "IVA 16%", amount: 16, amount_type: "percent", price_include: true, children_tax_ids: [], active: true };
const taxes = new Map([[16, iva]]);
const cash = { id: 10, name: "Efectivo", is_cash: true };

describe("wholesale price", () => {
  it("charges the price the cashier typed and goes back to the catalog price", () => {
    let cart = addProduct(emptyCart(newId), soda, newId, 12);
    const line = cart.lines[0]!.uuid;
    cart = setWholesalePrice(cart, line, 15.456);
    expect(cart.lines[0]).toMatchObject({ priceUnit: 15.46, wholesale: true });
    expect(unitPrice(cart.lines[0]!)).toBe(15.46);

    const pricing = localPricing(cart, taxes, false);
    expect(pricing.exact).toBe(true);
    expect(pricing.total).toBe(185.52);
    expect(pricing.lines[0]!.wholesale).toBe(true);

    cart = setWholesalePrice(cart, line, null);
    expect(cart.lines[0]!.wholesale).toBeUndefined();
    expect(unitPrice(cart.lines[0]!)).toBe(20);
  });

  it("ignores a price that is not a price, and new pieces do not join a wholesale line", () => {
    let cart = addProduct(emptyCart(newId), soda, newId, 2);
    const line = cart.lines[0]!.uuid;
    expect(setWholesalePrice(cart, line, 0)).toEqual(cart);
    expect(setWholesalePrice(cart, line, Number.NaN)).toEqual(cart);
    cart = setWholesalePrice(cart, line, 18);
    cart = addProduct(cart, soda, newId);
    expect(cart.lines.map((l) => [l.qty, l.wholesale ?? false])).toEqual([[2, true], [1, false]]);
  });

  it("travels to the server with its price and the wholesale mark", () => {
    let cart = addProduct(emptyCart(newId), soda, newId, 3);
    cart = setWholesalePrice(cart, cart.lines[0]!.uuid, 17);
    const pricing = localPricing(cart, taxes, false);
    const order = buildOrder(cart, pricing, [{ method: cash, amount: 51 }], {
      registerId: 1, sessionId: 2, employeeId: 3, employeeSession: null, now: new Date("2026-10-08T12:00:00Z"),
    });
    expect(order.lines[0]).toMatchObject({ product_id: 1, qty: 3, price_unit: 17, wholesale: true });
  });
});
