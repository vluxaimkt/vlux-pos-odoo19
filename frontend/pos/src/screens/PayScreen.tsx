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
import { Icon, type IconName } from "../ui/Icon";
import { Banner } from "../ui/Page";

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
  const done = state.complete;
  return (
    <section class="p-4 lg:p-8 max-w-2xl mx-auto flex flex-col gap-6 rise">
      <header class="grid grid-cols-[1fr_auto_1fr] items-center">
        <button class="btn btn-ghost text-primary -ml-2 justify-self-start" disabled={busy} onClick={onBack}>
          <Icon name="back" size={20} /> Venta
        </button>
        <h1 class="text-xl">Cobrar</h1>
      </header>

      <div class="surface p-6 flex flex-col items-center gap-2 text-center">
        <span class="label-2 font-medium">Total</span>
        <span class="display">{money(pricing.total)}</span>
        {pricing.source === "local" && (
          <span class="pill pill-plain"><Icon name="cloudOff" size={14} /> Calculado sin internet</span>
        )}
        <div class="mt-2 flex items-baseline gap-2">
          <span class="label-2">{state.change > 0 ? "Cambio" : done ? "Pagado" : "Falta"}</span>
          <span class={`text-2xl font-semibold num ${state.change > 0 ? "text-success" : done ? "text-success" : ""}`}>
            {money(state.change > 0 ? state.change : state.remaining)}
          </span>
        </div>
      </div>

      {payments.length > 0 && (
        <div class="grouped" aria-label="Pagos">
          {payments.map((payment, index) => (
            <div key={index} class="row">
              <span class="method-icon !w-8 !h-8 !rounded-[8px]" style={{ background: methodColor(payment.method) }}>
                <Icon name={methodIcon(payment.method)} size={18} />
              </span>
              <span class="flex-1 font-medium">{payment.method.name}</span>
              <span class="font-semibold num">{money(payment.amount)}</span>
              <button class="btn btn-ghost btn-sm btn-square label-2" aria-label="Quitar pago"
                onClick={() => setPayments(payments.filter((_, i) => i !== index))}><Icon name="close" size={16} /></button>
            </div>
          ))}
        </div>
      )}
      {state.problem && <Banner tone="error">{state.problem}</Banner>}
      {error && <Banner tone="error">{error}</Banner>}

      {!done && (
        <>
          {cashMethod && (
            <div class="flex flex-col gap-2">
              <span class="text-sm label-2 font-medium px-1">Efectivo rápido</span>
              <div class="flex flex-wrap gap-2">
                {cashSuggestions(state.remaining).map((value) => (
                  <button key={value} class="chip num" onClick={() => void pay(cashMethod, String(value))}>
                    {money(value)}
                  </button>
                ))}
              </div>
            </div>
          )}
          <label class="flex flex-col gap-2">
            <span class="text-sm label-2 font-medium px-1">Monto recibido</span>
            <input
              class="input input-lg w-full text-2xl num"
              type="number"
              inputMode="decimal"
              min="0"
              step="0.01"
              placeholder={money(state.remaining)}
              value={amount}
              onInput={(event) => setAmount(event.currentTarget.value)}
            />
            <span class="text-xs label-2 px-1">
              Vacío cobra lo que falta. Para pago mixto, escribe cuánto paga con una forma, tócala y paga el resto con otra.
            </span>
          </label>
          {creditMethod && !offerCredit && (
            <p class="text-xs label-2 px-1">
              {canSellOnCredit ? "Para fiar, elige al cliente en la venta." : "En esta caja sólo el encargado o el dueño pueden fiar."}
            </p>
          )}
          <div class="grid grid-cols-2 gap-4">
            {methods.map((method) => (
              <button key={method.id} class="method" disabled={state.remaining <= 0} onClick={() => void pay(method)}>
                <span class="method-icon" style={{ background: methodColor(method) }}>
                  <Icon name={methodIcon(method)} size={22} />
                </span>
                <span class="leading-tight">
                  {method.type === "pay_later" ? `Fiar a ${cart.customer?.name ?? ""}` : method.name}
                  {method.type !== "pay_later" && amount && <span class="block text-sm font-normal label-2 num">{money(Number(amount) || 0)}</span>}
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      <button class="btn btn-primary btn-xl w-full" disabled={!done || busy} onClick={() => void confirm()}>
        {busy ? <span class="loading loading-spinner" /> : <><Icon name="checkCircle" size={22} /> Terminar venta</>}
      </button>
    </section>
  );
}

/** Cash, card or credit: the icon and system color of each kind of payment method. */
function methodIcon(method: { is_cash: boolean; type?: string }): IconName {
  if (method.is_cash) return "cash";
  return method.type === "pay_later" ? "person" : "card";
}

function methodColor(method: { is_cash: boolean; type?: string }): string {
  if (method.is_cash) return "linear-gradient(180deg, #34c759, #248a3d)";
  return method.type === "pay_later" ? "linear-gradient(180deg, #ffb340, #ff9500)" : "linear-gradient(180deg, #5ac8fa, #007aff)";
}
