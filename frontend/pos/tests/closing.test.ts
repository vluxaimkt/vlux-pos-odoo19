import { describe, expect, it } from "vitest";

import type { ClosingSummary } from "../src/api/types";
import { closingRequest, countClosing } from "../src/sale/closing";

const summary: ClosingSummary = {
  session: { id: 31, name: "Caja 1/0005", state: "opened", opened_at: null, closed_at: null, opening_cash: 100, employee_id: 7, user_id: 2 },
  max_difference: 30,
  orders: { count: 2, amount: 115.5 },
  cash: { payment_method_id: 4, name: "Efectivo", opening: 100, sales: 60, moves: [], expected: 160 },
  other_methods: [
    { payment_method_id: 5, name: "Tarjeta", type: "bank", expected: 55.5, count: 1 },
    { payment_method_id: 6, name: "Crédito", type: "pay_later", expected: 20, count: 1 },
  ],
};

describe("closing count", () => {
  it("compares counted with expected for cash and card, ignoring credit", () => {
    const count = countClosing(summary, 150, new Map([[5, 55.5]]));
    expect(count.cash).toMatchObject({ expected: 160, counted: 150, difference: -10 });
    expect(count.others).toEqual([{ paymentMethodId: 5, name: "Tarjeta", expected: 55.5, counted: 55.5, difference: 0 }]);
    expect(count).toMatchObject({ largest: 10, withinLimit: true });
  });

  it("an uncounted card matches; above the limit only a manager may close", () => {
    expect(countClosing(summary, 110, new Map()).others[0]?.difference).toBe(0);
    expect(countClosing(summary, 110, new Map())).toMatchObject({ largest: 50, withinLimit: false });
    expect(countClosing(summary, 130, new Map())).toMatchObject({ largest: 30, withinLimit: true });
    expect(countClosing({ ...summary, max_difference: null }, 0, new Map()).withinLimit).toBe(true);
  });

  it("builds the request the server expects", () => {
    const count = countClosing(summary, 160.004, new Map([[5, 50]]));
    expect(closingRequest(summary, count, 7, "  todo bien ")).toEqual({
      session_id: 31, counted_cash: 160, counted: [{ payment_method_id: 5, amount: 50 }], employee_id: 7, notes: "todo bien",
    });
    expect(closingRequest({ ...summary, cash: null }, countClosing({ ...summary, cash: null }, 0, new Map()), null, ""))
      .toEqual({ session_id: 31, counted: [{ payment_method_id: 5, amount: 55.5 }] });
  });
});
