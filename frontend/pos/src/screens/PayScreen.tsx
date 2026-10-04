import { useState } from "preact/hooks";

import { isRetryable } from "../api/client";
import type { CreditRow, OrderRequest, PaymentMethod } from "../api/types";
import { formatMoney } from "../lib/money";
import type { Cart } from "../sale/cart";
import { creditRefusal, withBalance } from "../sale/credit";
import { round } from "../sale/money";
import { buildOrder } from "../sale/order";
import { addPayment, cashSuggestions, type Payment, paymentState } from "../sale/payment";
import type { Pricing } from "../sale/pricing";
import { usePos } from "../state";
import { enqueueSale } from "../sync/outbox";

/** What a sale on credit prints: the customer and the balances. */
export interface CreditTicket {
  name: string;
  previous: number;
  amount: number;
}

/**
 * Collect the payment. The sale is stored in the local queue before anything
 * is sent, so a network cut after this point loses nothing; the queue sends
 * it (with its uuid, so never twice).
 */
export function PayScreen({ cart, pricing, onBack, onPaid }: {
  cart: Cart;
  pricing: Pricing;
  onBack: () => void;
  onPaid: (order: OrderRequest, pricing: Pricing, credit: CreditTicket | null) => void;
}) {
  const { db, client, online, setup, employee, registerState, flushNow, credit, saveCredit, canSellOnCredit } = usePos();
  // "Crédito" (Odoo's customer account) only for the encargado/owner and a
  // chosen customer, as on the Odoo POS (D7).
  const creditMethod = setup.register.payment_methods.find((method) => method.type === "pay_later");
  const offerCredit = !!creditMethod && canSellOnCredit && !!cart.customer;
  const methods = setup.register.payment_methods.filter((method) => method.type !== "pay_later" || offerCredit);
  const [creditRow, setCreditRow] = useState<CreditRow | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const currency = setup.store.currency;
  const state = paymentState(pricing.total, payments);

  const money = (value: number) => formatMoney(value, currency);

  /** The customer's credit now: fresh from the server, or the last known offline. */
  async function creditStatus(partnerId: number): Promise<CreditRow | undefined> {
    if (online) {
      try {
        const fresh = await client.creditCustomer(partnerId);
        saveCredit(fresh);
        return fresh;
      } catch (err) {
        if (!isRetryable(err)) throw err;
      }
    }
    return credit.get(partnerId);
  }

  /** Pay `typed` (empty: what is left) with `method`. */
  async function pay(method: PaymentMethod, typed = amount) {
    const result = addPayment(pricing.total, payments, method, typed, currency.decimal_places);
    if (result.error !== null) {
      setError(result.error);
      return;
    }
    if (method.type === "pay_later" && cart.customer) {
      const onCredit = round(result.payments.filter((p) => p.method.id === method.id).reduce((t, p) => t + p.amount, 0));
      const row = await creditStatus(cart.customer.id);
      const refusal = creditRefusal(row, cart.customer.name, onCredit, money);
      if (refusal) {
        setError(refusal);
        return;
      }
      setCreditRow(row ?? null);
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
        employeeSession: client.employeeSession,
        now: new Date(),
      });
      await enqueueSale(db, order);
      void flushNow();
      const onCredit = round(payments.filter((p) => p.method.id === creditMethod?.id).reduce((t, p) => t + p.amount, 0));
      let creditTicket: CreditTicket | null = null;
      if (onCredit > 0 && cart.customer && creditRow) {
        // The register's copy moves now; the server's figures replace it on sync.
        saveCredit(withBalance(creditRow, creditRow.balance + onCredit));
        creditTicket = { name: cart.customer.name, previous: creditRow.balance, amount: onCredit };
      }
      onPaid(order, pricing, creditTicket);
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
                <button key={value} class="btn" onClick={() => void pay(cashMethod, String(value))}>
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
          {creditMethod && !offerCredit && (
            <p class="text-xs opacity-70">
              {canSellOnCredit ? "Para fiar, elige al cliente en la venta." : "En esta caja sólo el encargado o el dueño pueden fiar."}
            </p>
          )}
          <div class="grid grid-cols-2 gap-2">
            {methods.map((method) => (
              <button key={method.id} class={`btn btn-lg ${method.type === "pay_later" ? "btn-warning btn-outline" : "btn-outline"}`}
                disabled={state.remaining <= 0} onClick={() => void pay(method)}>
                {method.type === "pay_later" ? `Fiar a ${cart.customer?.name ?? ""}` : `Pagar con ${method.name}`}
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
