import { describe, expect, it } from "vitest";

import type { Employee } from "../src/api/types";
import { pinMatches, sha1Hex } from "../src/lib/pin";
import { formatMoney } from "../src/lib/money";

const employee = (pin_sha1: string | null): Employee => ({
  id: 1, name: "Cajera", role: "cashier", user_id: null, pin_sha1, barcode_sha1: null,
});

describe("PIN", () => {
  it("hashes like Odoo (SHA-1 hex)", async () => {
    expect(await sha1Hex("1234")).toBe("7110eda4d09e062aa5e4a390b0a572ac0d2c0220");
  });

  it("checks a PIN offline; no PIN means no check", async () => {
    const hashed = employee(await sha1Hex("2580"));
    expect(await pinMatches(hashed, "2580")).toBe(true);
    expect(await pinMatches(hashed, "0000")).toBe(false);
    expect(await pinMatches(employee(null), "")).toBe(true);
  });
});

describe("money", () => {
  it("formats pesos with the currency's decimals", () => {
    expect(formatMoney(1234.5, { name: "MXN", decimal_places: 2 })).toBe("$1,234.50");
  });
});
