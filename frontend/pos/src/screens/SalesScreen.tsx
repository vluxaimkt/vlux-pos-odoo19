import { useEffect, useState } from "preact/hooks";

import type { OrderResult } from "../api/types";
import { formatDateTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { round } from "../sale/money";
import { usePos } from "../state";
import { explain } from "./SetupScreen";

type Mode =
  | { name: "list" }
  | { name: "detail"; order: OrderResult }
  | { name: "ticket"; order: OrderResult };

/**
 * Today's sales of the register: find one by folio, reprint its ticket or
 * return products (as the Odoo POS "Pedidos" screen). Needs the network.
 */
export function SalesScreen({ onClose }: { onClose: () => void }) {
  const { client, setup, online } = usePos();
  const [mode, setMode] = useState<Mode>({ name: "list" });
  const [orders, setOrders] = useState<OrderResult[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const money = (n: number) => formatMoney(n, setup.store.currency);

  async function load(reference = "") {
    setBusy(true);
    setError(null);
    try {
      const result = reference.trim().length >= 3
        ? await client.lookupOrder(reference.trim())
        : await client.recentOrders(setup.register.id);
      setOrders(result.items);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (online) void load();
  }, [online]);

  if (mode.name === "detail") {
    return <ReturnForm order={mode.order} onBack={() => setMode({ name: "list" })}
      onReturned={(refund) => setMode({ name: "ticket", order: refund })} />;
  }
  if (mode.name === "ticket") {
    return <OrderTicket order={mode.order} onDone={() => { setMode({ name: "list" }); void load(); }} />;
  }

  return (
    <section class="p-4 max-w-3xl mx-auto flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <h2 class="text-xl flex-1">Ventas y devoluciones</h2>
        <button class="btn btn-ghost btn-sm" onClick={onClose}>Volver a vender</button>
      </div>
      {!online && <div role="alert" class="alert alert-warning">Las ventas del día y las devoluciones necesitan internet.</div>}
      <form class="join w-full" onSubmit={(e) => { e.preventDefault(); void load(query); }}>
        <input class="input join-item w-full" type="search" placeholder="Folio del ticket (p. ej. 260-4-000008)"
          value={query} onInput={(e) => setQuery(e.currentTarget.value)} />
        <button class="btn join-item" disabled={busy || !online}>Buscar</button>
      </form>
      {error && <div role="alert" class="alert alert-error">{error}</div>}
      {busy && <span class="loading loading-spinner" />}
      <ul class="list bg-base-100 rounded-box">
        {orders.map((order) => (
          <li key={order.id} class="list-row items-center">
            <div class="list-col-grow">
              <div class="font-semibold">
                {order.pos_reference ?? order.name}
                {order.is_refund && <span class="badge badge-warning badge-sm ml-2">Devolución</span>}
              </div>
              <div class="text-xs opacity-70">
                {formatDateTime(order.date_order.replace(" ", "T"))}
                {order.lines.length > 0 && ` · ${order.lines.length} producto(s)`}
              </div>
            </div>
            <div class="font-semibold">{money(order.amount_total)}</div>
            <div class="flex gap-1">
              <button class="btn btn-sm" onClick={() => setMode({ name: "ticket", order })}>Reimprimir</button>
              {!order.is_refund && order.lines.some((line) => line.refundable_qty > 0) && (
                <button class="btn btn-sm btn-warning" onClick={() => setMode({ name: "detail", order })}>Devolver</button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {!busy && !orders.length && online && <p class="opacity-60">No hay ventas que mostrar.</p>}
    </section>
  );
}

/** Choose what comes back and how the money goes back; the server prices it with the sale's prices. */
function ReturnForm({ order, onBack, onReturned }: { order: OrderResult; onBack: () => void; onReturned: (refund: OrderResult) => void }) {
  const { client, setup } = usePos();
  const [qty, setQty] = useState<Record<number, string>>({});
  const [amount, setAmount] = useState<number | null>(null);
  const [paidWithCredit, setPaidWithCredit] = useState(false);
  const [methodId, setMethodId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // One id per return: a retry after a dropped connection is the same return.
  const [uuid] = useState(() => crypto.randomUUID());
  const money = (n: number) => formatMoney(n, setup.store.currency);
  const lines = Object.entries(qty)
    .map(([id, value]) => ({ line_id: Number(id), qty: Number(value.replace(",", ".")) }))
    .filter((line) => line.qty > 0);
  const methods = setup.register.payment_methods.filter((m) => m.type !== "pay_later" || paidWithCredit);

  async function calculate() {
    setError(null);
    setAmount(null);
    if (!lines.length) return setError("Escribe cuántas piezas regresan.");
    setBusy(true);
    try {
      const quote = await client.refundQuote({ register_id: setup.register.id, order_id: order.id, lines });
      setAmount(quote.amount_refund);
      const credit = quote.paid_with.some((p) => p.type === "pay_later");
      setPaidWithCredit(credit);
      // By default the money goes back the way it came in.
      const first = quote.paid_with.find((p) => p.amount > 0);
      setMethodId(first?.payment_method_id ?? setup.register.payment_methods.find((m) => m.is_cash)?.id ?? null);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (amount === null || !methodId) return;
    setBusy(true);
    setError(null);
    try {
      const refund = await client.createRefund({
        uuid, register_id: setup.register.id, order_id: order.id, lines,
        payments: [{ payment_method_id: methodId, amount: round(amount) }],
      });
      onReturned(refund);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section class="p-4 max-w-2xl mx-auto flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <h2 class="text-xl flex-1">Devolución · {order.pos_reference ?? order.name}</h2>
        <button class="btn btn-ghost btn-sm" onClick={onBack}>Volver</button>
      </div>
      <ul class="list bg-base-100 rounded-box">
        {order.lines.filter((line) => line.qty > 0).map((line) => (
          <li key={line.id} class="list-row items-center">
            <div class="list-col-grow">
              <div>{line.name}</div>
              <div class="text-xs opacity-70">
                Vendidas {line.qty} · se pueden devolver {line.refundable_qty} · {money(line.price_subtotal_incl / line.qty)} c/u
              </div>
            </div>
            <input class="input input-sm w-24" type="number" inputMode="decimal" min="0" max={line.refundable_qty} step="any"
              disabled={line.refundable_qty <= 0} placeholder="0" value={qty[line.id] ?? ""}
              onInput={(e) => { setQty({ ...qty, [line.id]: e.currentTarget.value }); setAmount(null); }} />
          </li>
        ))}
      </ul>
      {error && <div role="alert" class="alert alert-error">{error}</div>}
      {amount === null ? (
        <button class="btn btn-primary" disabled={busy || !lines.length} onClick={() => void calculate()}>Calcular devolución</button>
      ) : (
        <>
          <div class="stats bg-base-100 shadow">
            <div class="stat"><div class="stat-title">Se devuelve</div><div class="stat-value">{money(amount)}</div></div>
          </div>
          <div class="flex flex-wrap gap-2">
            {methods.map((method) => (
              <button key={method.id} class={`btn ${methodId === method.id ? "btn-primary" : "btn-outline"}`}
                onClick={() => setMethodId(method.id)}>
                {method.type === "pay_later" ? "A su cuenta (crédito)" : method.name}
              </button>
            ))}
          </div>
          <button class="btn btn-warning btn-lg" disabled={busy || !methodId} onClick={() => void confirm()}>
            Devolver {money(amount)}
          </button>
        </>
      )}
    </section>
  );
}

/** The unit price as the customer saw it: the shelf price when taxes are included (weights: $17.40/kg, not total / kilos). */
function shelfPrice(line: OrderResult["lines"][number]): number {
  if (!line.qty) return 0;
  return Math.abs(round(line.price_unit * line.qty) - line.price_subtotal_incl) < 0.005
    ? Math.abs(line.price_unit) : line.price_subtotal_incl / line.qty;
}

/** A ticket rebuilt from what the server recorded: reprints and return slips. */
export function OrderTicket({ order, onDone }: { order: OrderResult; onDone: () => void }) {
  const { setup } = usePos();
  const company = setup.store.company;
  const money = (n: number) => formatMoney(n, setup.store.currency);
  return (
    <section class="p-4 flex flex-col items-center gap-4">
      <article class="receipt bg-white text-black font-mono text-sm p-4 w-[80mm] max-w-full shadow print:shadow-none">
        <header class="text-center">
          <div class="font-bold text-base">{company.name}</div>
          {company.vat && <div>RFC: {company.vat}</div>}
          {company.fiscal_regime && <div>{company.fiscal_regime}</div>}
        </header>
        <hr class="my-2 border-dashed border-black" />
        <div class="text-center font-bold">{order.is_refund ? "DEVOLUCIÓN" : "REIMPRESIÓN DE TICKET"}</div>
        <div>{formatDateTime(order.date_order.replace(" ", "T"))}</div>
        <div>{setup.register.name}</div>
        <div>Folio: {order.pos_reference ?? order.name}</div>
        <hr class="my-2 border-dashed border-black" />
        {order.lines.map((line) => (
          <div key={line.id}>
            <div>{line.name}</div>
            <div class="flex justify-between">
              <span>{line.qty} x {money(shelfPrice(line))}</span>
              <span>{money(line.price_subtotal_incl)}</span>
            </div>
          </div>
        ))}
        <hr class="my-2 border-dashed border-black" />
        <div class="flex justify-between font-bold text-base"><span>{order.is_refund ? "DEVUELTO" : "TOTAL"}</span><span>{money(Math.abs(order.amount_total))}</span></div>
        {order.payments.filter((p) => !p.is_change).map((p, index) => (
          <div key={index} class="flex justify-between"><span>{p.name}</span><span>{money(Math.abs(p.amount))}</span></div>
        ))}
        {order.change > 0 && <div class="flex justify-between"><span>Cambio</span><span>{money(order.change)}</span></div>}
        {order.is_refund && (
          <div class="text-center pt-8">
            <div class="border-t border-black mx-4" />
            <div class="text-xs">Firma de quien recibe</div>
          </div>
        )}
        <hr class="my-2 border-dashed border-black" />
        {company.receipt_legend && <div class="text-center text-xs">{company.receipt_legend}</div>}
      </article>
      <div class="flex gap-2 print:hidden">
        <button class="btn btn-lg" onClick={() => window.print()}>Imprimir</button>
        <button class="btn btn-primary btn-lg" onClick={onDone}>Listo</button>
      </div>
    </section>
  );
}
