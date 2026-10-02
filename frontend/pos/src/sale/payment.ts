import type { PaymentMethod } from "../api/types";
import { round, sum } from "./money";

export interface Payment {
  method: Pick<PaymentMethod, "id" | "name" | "is_cash">;
  amount: number;
}

export interface PaymentState {
  paid: number;
  /** Still to collect (0 when covered). */
  remaining: number;
  /** Cash to hand back. */
  change: number;
  /** Covered, and any excess can be given back in cash. */
  complete: boolean;
  problem: string | null;
}

/**
 * Where the payments stand against `total`. Mirrors the server's rules
 * (POST /orders): the payments must cover the total, and change can only
 * come out of cash, so a card can never be charged more than what is left.
 */
export function paymentState(total: number, payments: Payment[]): PaymentState {
  const paid = sum(payments.map((payment) => payment.amount));
  const cash = sum(payments.filter((payment) => payment.method.is_cash).map((payment) => payment.amount));
  const excess = round(paid - total);
  if (excess < 0) return { paid, remaining: round(-excess), change: 0, complete: false, problem: null };
  if (excess > cash) {
    return { paid, remaining: 0, change: 0, complete: false, problem: "Con tarjeta no se puede cobrar de más: sólo el efectivo da cambio." };
  }
  return { paid, remaining: 0, change: excess, complete: true, problem: null };
}

/** Quick amounts a customer usually hands over, for the cash keypad. */
export function cashSuggestions(remaining: number): number[] {
  if (remaining <= 0) return [];
  const bills = [20, 50, 100, 200, 500, 1000];
  const options = new Set<number>([round(remaining)]);
  for (const bill of bills) {
    const rounded = Math.ceil(remaining / bill) * bill;
    if (rounded > remaining) options.add(rounded);
    if (options.size >= 5) break;
  }
  return [...options].sort((a, b) => a - b);
}

export type AddPayment = { payments: Payment[]; error: null } | { payments: null; error: string };

/**
 * Add a payment with `method`. An empty amount pays what is left. Cash may
 * exceed it (the excess is change); any other method may not, so a mixed
 * payment is: type what one method covers, pick it, then pay the rest.
 */
export function addPayment(total: number, payments: Payment[], method: Payment["method"], typed: string, decimals = 2): AddPayment {
  const { remaining } = paymentState(total, payments);
  if (remaining <= 0) return { payments: null, error: "La venta ya está pagada." };
  const text = typed.trim().replace(",", ".");
  const amount = text === "" ? remaining : round(Number(text), decimals);
  if (!Number.isFinite(amount) || amount <= 0) return { payments: null, error: "Escribe un monto mayor que cero." };
  if (!method.is_cash && amount > remaining) {
    return { payments: null, error: `Con ${method.name} no se puede cobrar más de lo que falta.` };
  }
  return { payments: [...payments, { method, amount }], error: null };
}
