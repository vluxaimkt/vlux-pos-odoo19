import type { OrderRequest } from "../api/types";
import type { Cart } from "./cart";
import type { Payment } from "./payment";
import type { Pricing } from "./pricing";

export interface SaleContext {
  registerId: number;
  sessionId: number | null;
  employeeId: number | null;
  /** The server-checked employee session at the time of the sale, if any. */
  employeeSession?: string | null;
  now: Date;
}

/**
 * The POST /orders body for a paid cart.
 *
 * Each line carries the unit price the customer was charged, so the server
 * books exactly that (and flags it if it differs from the catalog), and
 * `expected_total` makes the server refuse, rather than silently change, a
 * total that does not match what was charged. Only exact pricings get here.
 */
export function buildOrder(cart: Cart, pricing: Pricing, payments: Payment[], context: SaleContext): OrderRequest {
  if (!pricing.exact) throw new Error("Este total no es exacto: la venta necesita internet.");
  if (!cart.lines.length) throw new Error("La venta no tiene productos.");
  return {
    uuid: cart.uuid,
    register_id: context.registerId,
    ...(context.sessionId ? { session_id: context.sessionId } : {}),
    ...(context.employeeId ? { employee_id: context.employeeId } : {}),
    ...(cart.customer ? { partner_id: cart.customer.id } : {}),
    ...(context.employeeSession ? { employee_session: context.employeeSession } : {}),
    lines: pricing.lines.map((line) => ({
      uuid: line.lineUuid,
      product_id: line.productId,
      qty: line.qty,
      price_unit: line.priceUnit,
    })),
    payments: payments.map((payment) => ({ payment_method_id: payment.method.id, amount: payment.amount })),
    expected_total: pricing.total,
    created_at: context.now.toISOString(),
  };
}
