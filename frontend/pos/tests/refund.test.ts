import { describe, expect, it } from "vitest";

import { refundNeedsManager } from "../src/sale/refund";

describe("who may return a sale", () => {
  const cashier = { role: "cashier" as const };
  const manager = { role: "manager" as const };

  it("asks a cashier for a manager's PIN unless the store turned it off", () => {
    expect(refundNeedsManager(true, cashier, { refunds_need_manager: true })).toBe(true);
    expect(refundNeedsManager(true, cashier, {})).toBe(true);
    expect(refundNeedsManager(true, cashier, undefined)).toBe(true);
    expect(refundNeedsManager(true, cashier, { refunds_need_manager: false })).toBe(false);
  });

  it("lets managers return directly, and registers without employee login act as the user", () => {
    expect(refundNeedsManager(true, manager, { refunds_need_manager: true })).toBe(false);
    expect(refundNeedsManager(false, cashier, { refunds_need_manager: true })).toBe(false);
    expect(refundNeedsManager(true, null, { refunds_need_manager: true })).toBe(false);
  });
});
