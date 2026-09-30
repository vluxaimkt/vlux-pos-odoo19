import type { ClosingSummary } from "../api/types";
import { round } from "./money";

export interface CountedLine {
  paymentMethodId: number;
  name: string;
  expected: number;
  counted: number;
  difference: number;
}

export interface ClosingCount {
  cash: CountedLine | null;
  others: CountedLine[];
  /** Largest absolute difference: what the closing limit is judged on. */
  largest: number;
  /** Whether a cashier may close (the server decides; this only warns early). */
  withinLimit: boolean;
}

/**
 * Compare what was counted with what the server expects. `counted` holds the
 * amounts typed for non-cash methods; a method left out counts as matching,
 * as in the Odoo closing popup. Only "bank" methods are counted (card
 * terminals); the server ignores the rest.
 */
export function countClosing(summary: ClosingSummary, countedCash: number, counted: Map<number, number>): ClosingCount {
  const line = (paymentMethodId: number, name: string, expected: number, amount: number): CountedLine => ({
    paymentMethodId, name, expected, counted: round(amount), difference: round(amount - expected),
  });
  const cash = summary.cash ? line(summary.cash.payment_method_id, summary.cash.name, summary.cash.expected, countedCash) : null;
  const others = summary.other_methods
    .filter((method) => method.type === "bank")
    .map((method) => line(method.payment_method_id, method.name, method.expected, counted.get(method.payment_method_id) ?? method.expected));
  const largest = Math.max(0, ...[cash, ...others].filter((l): l is CountedLine => !!l).map((l) => Math.abs(l.difference)));
  const withinLimit = summary.max_difference === null || largest <= summary.max_difference + 1e-9;
  return { cash, others, largest: round(largest), withinLimit };
}

/** The body of POST /registers/<id>/session/close. */
export function closingRequest(summary: ClosingSummary, count: ClosingCount, employeeId: number | null, notes: string) {
  return {
    session_id: summary.session.id,
    ...(count.cash ? { counted_cash: count.cash.counted } : {}),
    counted: count.others.map((line) => ({ payment_method_id: line.paymentMethodId, amount: line.counted })),
    ...(employeeId ? { employee_id: employeeId } : {}),
    ...(notes.trim() ? { notes: notes.trim() } : {}),
  };
}
