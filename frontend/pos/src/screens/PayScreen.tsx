import { useState } from "preact/hooks";

import type { OrderRequest, PaymentMethod } from "../api/types";
import { formatMoney } from "../lib/money";
import type { Cart } from "../sale/cart";
import { buildOrder } from "../sale/order";
import { addPayment, cashSuggestions, type Payment, paymentState } from "../sale/payment";
import type { Pricing } from "../sale/pricing";
import { usePos } from "../state";
import { enqueueSale } from "../sync/outbox";

/**
 * Collect the payment. The sale is stored in the local queue before anything
 * is sent, so a network cut after this point loses nothing; the queue sends
 * it (with its uuid, so never twice).
 */
export function PayScreen({ cart, pricing, onBack, onPaid }: {
  cart: Cart;
  pricing: Pricing;
  onBack: () => void;
  onPaid: (order: OrderRequest, pricing: Pricing) => void;
}) {
  const { db, setup, employee, registerState, flushNow } = usePos();
  const methods = setup.register.payment_methods.filter((method) => method.type !== "pay_later");
  const [payments, setPayments] = useState<Payment[]>([]);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const currency = setup.store.currency;
  const state = paymentState(pricing.total, payments);

  /** Pay `typed` (empty: what is left) with `method`. */
  function pay(method: PaymentMethod, typed = amount) {
    const result = addPayment(pricing.total, payments, method, typed, currency.decimal_places);
    if (result.error !== null) {
      setError(result.error);
      return;
    }
    setPayments(result.payments);
    setAmount("");
    setError(null);
  }

  async function confirm() {
    if (!state.complete) return;
    setBusy(true);
    try {
      const order = buildOrder(cart, pricing, payments, {
        registerId: setup.register.id,
        sessionId: registerState?.session?.id ?? null,
        employeeId: employee?.id ?? null,
        now: new Date(),
      });
      await enqueueSale(db, order);
      void flushNow();
      onPaid(order, pricing);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  const cashMethod = methods.find((method) => method.is_cash);
  return (
    <section class="p-4 max-w-xl mx-auto flex flex-col gap-4">
      <div class="stats shadow bg-base-100">
        <div class="stat">
          <div class="stat-title">Total</div>
          <div class="stat-value">{formatMoney(pricing.total, currency)}</div>
          {pricing.source === "local" && <div class="stat-desc">Calculado sin internet</div>}
        </div>
        <div class="stat">
          <div class="stat-title">{state.change > 0 ? "Cambio" : "Falta"}</div>
          <div class={`stat-value ${state.change > 0 ? "text-success" : ""}`}>
            {formatMoney(state.change > 0 ? state.change : state.remaining, currency)}
          </div>
        </div>
      </div>

      {payments.length > 0 && (
        <ul class="list bg-base-100 rounded-box">
          {payments.map((payment, index) => (
            <li key={index} class="list-row items-center">
              <div class="list-col-grow">{payment.method.name}</div>
              <div>{formatMoney(payment.amount, currency)}</div>
              <button class="btn btn-ghost btn-xs" aria-label="Quitar pago"
                onClick={() => setPayments(payments.filter((_, i) => i !== index))}>✕</button>
            </li>
          ))}
        </ul>
      )}
      {state.problem && <div role="alert" class="alert alert-error">{state.problem}</div>}
      {error && <div role="alert" class="alert alert-error">{error}</div>}

      {!state.complete && (
        <>
          {cashMethod && (
            <div class="flex flex-wrap gap-2">
              {cashSuggestions(state.remaining).map((value) => (
                <button key={value} class="btn" onClick={() => pay(cashMethod, String(value))}>
                  {formatMoney(value, currency)}
                </button>
              ))}
            </div>
          )}
          <fieldset class="fieldset">
            <legend class="fieldset-legend">Monto (vacío = lo que falta)</legend>
            <input
              class="input input-lg w-full"
              type="number"
              inputMode="decimal"
              min="0"
              step="0.01"
              placeholder={String(state.remaining)}
              value={amount}
              onInput={(event) => setAmount(event.currentTarget.value)}
            />
            <p class="label whitespace-normal">
              Pago mixto: escribe cuánto paga con una forma (p. ej. 50), tócala, y paga lo que falta con otra.
            </p>
          </fieldset>
          <div class="grid grid-cols-2 gap-2">
            {methods.map((method) => (
              <button key={method.id} class="btn btn-lg btn-outline" disabled={state.remaining <= 0} onClick={() => pay(method)}>
                Pagar con {method.name}
              </button>
            ))}
          </div>
        </>
      )}

      <div class="flex gap-2">
        <button class="btn btn-ghost flex-1" disabled={busy} onClick={onBack}>Volver</button>
        <button class="btn btn-primary btn-lg flex-[2]" disabled={!state.complete || busy} onClick={() => void confirm()}>
          {busy ? <span class="loading loading-spinner" /> : "Terminar venta"}
        </button>
      </div>
    </section>
  );
}
