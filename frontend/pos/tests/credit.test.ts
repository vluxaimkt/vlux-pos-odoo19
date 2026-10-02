import { describe, expect, it } from "vitest";

import type { CreditRow } from "../src/api/types";
import { canSellOnCredit, creditRefusal, withBalance } from "../src/sale/credit";

const money = (n: number) => `$${n.toFixed(2)}`;
const row = (extra: Partial<CreditRow> = {}): CreditRow => ({
  partner_id: 1, name: "Lupe", phone: null, allowed: true, limit: 100, balance: 40, available: 60, over_limit: false, ...extra,
});

describe("credit rules", () => {
  it("only managers sell on credit when the register has employee login", () => {
    expect(canSellOnCredit(true, "manager")).toBe(true);
    expect(canSellOnCredit(true, "cashier")).toBe(false);
    expect(canSellOnCredit(false, null)).toBe(true);
  });

  it("refuses unauthorised customers and sales above the limit, like the Odoo POS", () => {
    expect(creditRefusal(undefined, "Lupe", 10, money)).toContain("no tiene crédito autorizado");
    expect(creditRefusal(row({ allowed: false }), "Lupe", 10, money)).toContain("no tiene crédito autorizado");
    expect(creditRefusal(row(), "Lupe", 60, money)).toBeNull(); // exactly the limit
    expect(creditRefusal(row(), "Lupe", 60.01, money)).toContain("lo rebasa");
    expect(creditRefusal(row({ limit: 0, available: null }), "Lupe", 5000, money)).toBeNull(); // no limit
  });

  it("keeps the local copy's available and over-limit in step", () => {
    expect(withBalance(row(), 120)).toMatchObject({ balance: 120, available: -20, over_limit: true });
    expect(withBalance(row({ limit: 0, available: null }), 10)).toMatchObject({ available: null, over_limit: false });
  });
});
