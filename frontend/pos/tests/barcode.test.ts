import { describe, expect, it } from "vitest";

import { checkDigit, type Nomenclature, parseBarcode } from "../src/lib/barcode";

// Odoo's default POS nomenclature plus a weight rule, as a store would set it.
const nomenclature: Nomenclature = {
  upc_ean_conv: "always",
  rules: [
    { type: "weight", encoding: "ean13", pattern: "21.....{NNDDD}", sequence: 10, alias: null },
    { type: "price", encoding: "ean13", pattern: "23.....{NNNDD}", sequence: 14, alias: null },
    { type: "discount", encoding: "any", pattern: "22{NN}", sequence: 20, alias: null },
    { type: "product", encoding: "any", pattern: ".*", sequence: 90, alias: null },
  ],
};

function ean(prefix12: string): string {
  return prefix12 + checkDigit(prefix12 + "0");
}

describe("barcode nomenclature", () => {
  it("computes the EAN check digit like Odoo", () => {
    expect(checkDigit("7501055300846")).toBe(6);
    expect(checkDigit("2000000000060")).toBe(0);
  });

  it("reads the weight of a scale label and finds the product by its base code", () => {
    const label = ean("212345601250"); // product 12345, 1.250 kg
    const parsed = parseBarcode(label, nomenclature);
    expect(parsed.type).toBe("weight");
    expect(parsed.value).toBeCloseTo(1.25, 6);
    expect(parsed.baseCode).toBe(ean("212345600000"));
  });

  it("reads the price of a price label", () => {
    const label = ean("230042003750"); // product 00420, $37.50
    const parsed = parseBarcode(label, nomenclature);
    expect(parsed).toMatchObject({ type: "price", value: 37.5, baseCode: ean("230042000000") });
  });

  it("plain products and broken labels fall through to the product rule", () => {
    expect(parseBarcode("7501055300846", nomenclature)).toMatchObject({ type: "product", baseCode: "7501055300846" });
    const badCheck = "2123456012509"; // wrong check digit: not a valid EAN-13 weight label
    expect(parseBarcode(badCheck, nomenclature).type).toBe("product");
    expect(parseBarcode("abc", null).type).toBe("error");
  });
});
