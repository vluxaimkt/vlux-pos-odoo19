import { useEffect, useState } from "preact/hooks";

import type { OrderResult } from "../api/types";
import { formatDateTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { round } from "../sale/money";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { Banner, EmptyState, Field, Loading, PageHeader, Section, SLIP, SlipActions, SuccessHeader } from "../ui/Page";
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
    <section class="p-4 lg:p-8 max-w-3xl mx-auto flex flex-col gap-6 rise">
      <PageHeader title="Ventas y devoluciones" icon="receipt" back={onClose} />
      {!online && <Banner tone="warn">Las ventas del día y las devoluciones necesitan internet.</Banner>}
      <form class="flex gap-2" onSubmit={(e) => { e.preventDefault(); void load(query); }}>
        <label class="search-field flex-1">
          <Icon name="search" size={22} />
          <input type="search" placeholder="Folio del ticket (p. ej. 260-4-000008)" aria-label="Folio del ticket"
            value={query} onInput={(e) => setQuery(e.currentTarget.value)} />
        </label>
        <button class="btn btn-primary h-14 rounded-[16px] px-6" disabled={busy || !online}>Buscar</button>
      </form>
      {error && <Banner tone="error">{error}</Banner>}
      {busy ? <Loading /> : orders.length > 0 ? (
        <Section title={query.trim().length >= 3 ? "Resultado" : "Ventas recientes de esta caja"}>
          {orders.map((order) => (
            <div key={order.id} class="row !py-3">
              <span class="avatar-disc w-10 h-10 shrink-0" data-role={order.is_refund ? undefined : "manager"} aria-hidden="true">
                <Icon name={order.is_refund ? "back" : "receipt"} size={20} />
              </span>
              <div class="flex-1 min-w-0">
                <div class="font-semibold flex items-center gap-2">
                  <span class="truncate">{order.pos_reference ?? order.name}</span>
                  {order.is_refund && <span class="pill pill-plain pill-warn">Devolución</span>}
                </div>
                <div class="text-xs label-2">
                  {formatDateTime(order.date_order.replace(" ", "T"))}
                  {order.lines.length > 0 && ` · ${order.lines.length} producto(s)`}
                </div>
              </div>
              <span class="font-semibold num">{money(order.amount_total)}</span>
              <div class="flex gap-2">
                <button class="btn btn-sm" onClick={() => setMode({ name: "ticket", order })} aria-label="Reimprimir" title="Reimprimir">
                  <Icon name="printer" size={16} /><span class="hidden sm:inline">Reimprimir</span>
                </button>
                {!order.is_refund && order.lines.some((line) => line.refundable_qty > 0) && (
                  <button class="btn btn-sm btn-primary" onClick={() => setMode({ name: "detail", order })}>Devolver</button>
                )}
              </div>
            </div>
          ))}
        </Section>
      ) : online && <EmptyState icon="receipt" title="No hay ventas que mostrar" hint="Busca un ticket por su folio." />}
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
    <section class="p-4 lg:p-8 max-w-2xl mx-auto flex flex-col gap-6 rise">
      <PageHeader title="Devolución" subtitle={order.pos_reference ?? order.name} icon="back" back={onBack} backLabel="Ventas" />
      <Section title="¿Qué regresa?" footer="Escribe cuántas piezas (o kilos) regresan de cada producto.">
        {order.lines.filter((line) => line.qty > 0).map((line) => (
          <div key={line.id} class="row !py-3">
            <div class="flex-1 min-w-0">
              <div class="font-medium truncate">{line.name}</div>
              <div class="text-xs label-2 num">
                Vendidas {line.qty} · se pueden devolver {line.refundable_qty} · {money(line.price_subtotal_incl / line.qty)} c/u
              </div>
            </div>
            <input class="input w-24 text-right num" type="number" inputMode="decimal" min="0" max={line.refundable_qty} step="any"
              aria-label={`Cantidad que regresa de ${line.name}`}
              disabled={line.refundable_qty <= 0} placeholder="0" value={qty[line.id] ?? ""}
              onInput={(e) => { setQty({ ...qty, [line.id]: e.currentTarget.value }); setAmount(null); }} />
          </div>
        ))}
      </Section>
      {error && <Banner tone="error">{error}</Banner>}
      {amount === null ? (
        <button class="btn btn-primary btn-xl" disabled={busy || !lines.length} onClick={() => void calculate()}>
          {busy ? <span class="loading loading-spinner" /> : "Calcular devolución"}
        </button>
      ) : (
        <>
          <div class="surface p-6 flex flex-col items-center gap-1 text-center rise">
            <span class="label-2 font-medium">Se devuelve</span>
            <span class="display">{money(amount)}</span>
          </div>
          <Field label="¿Cómo se devuelve el dinero?">
            <div class="segmented" role="radiogroup" aria-label="Forma de devolución">
              {methods.map((method) => (
                <button key={method.id} type="button" role="radio" aria-selected={methodId === method.id} aria-checked={methodId === method.id}
                  onClick={() => setMethodId(method.id)}>
                  {method.type === "pay_later" ? "A su cuenta (crédito)" : method.name}
                </button>
              ))}
            </div>
          </Field>
          <button class="btn btn-primary btn-xl" disabled={busy || !methodId} onClick={() => void confirm()}>
            {busy ? <span class="loading loading-spinner" /> : `Devolver ${money(amount)}`}
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
    <section class="p-4 lg:p-8 flex flex-col items-center gap-6">
      <SuccessHeader title={order.is_refund ? "Devolución registrada" : "Reimpresión"} icon={order.is_refund ? "check" : "printer"}>
        <span class="num">{order.pos_reference ?? order.name} · {money(Math.abs(order.amount_total))}</span>
      </SuccessHeader>
      <article class={SLIP} style={{ animationDelay: "100ms" }}>
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
      <SlipActions onDone={onDone} />
    </section>
  );
}
