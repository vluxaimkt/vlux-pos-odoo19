import { useEffect, useState } from "preact/hooks";

import type { OrderRequest, OrderResult } from "../api/types";
import { formatDateTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { qtyLabel } from "../sale/cart";
import { paymentState } from "../sale/payment";
import type { Pricing } from "../sale/pricing";
import { usePos } from "../state";
import type { CreditTicket } from "./PayScreen";
import { Icon } from "../ui/Icon";

/**
 * The ticket. It is printed from what was charged; once the server has the
 * sale, its folio replaces the "pending" mark. Offline, the tax split shown
 * is the register's own calculation (the server's is the one booked).
 */
export function ReceiptScreen({ order, pricing, credit, onNext }: {
  order: OrderRequest;
  pricing: Pricing;
  /** Set for a sale on credit: who owes it and the balance before. */
  credit?: CreditTicket | null;
  onNext: () => void;
}) {
  const { db, setup, employee } = usePos();
  const [result, setResult] = useState<OrderResult | null>(null);
  const currency = setup.store.currency;
  const company = setup.store.company;
  const register = setup.register;
  const methods = new Map(register.payment_methods.map((method) => [method.id, method]));
  const payments = order.payments.map((payment) => {
    const method = methods.get(payment.payment_method_id);
    return { method: { id: payment.payment_method_id, name: method?.name ?? "Pago", is_cash: !!method?.is_cash }, amount: payment.amount };
  });
  const { change } = paymentState(pricing.total, payments);

  useEffect(() => {
    const check = async () => setResult((await db.outbox.get(order.uuid))?.result ?? null);
    void check();
    const id = setInterval(() => void check(), 2000);
    return () => clearInterval(id);
  }, [db, order.uuid]);

  const tax = result?.amount_tax ?? pricing.tax;
  // The server's figures once synced; the register's last known balance before.
  const previous = result?.credit?.previous_balance ?? credit?.previous ?? 0;
  const onCredit = result?.credit?.amount ?? credit?.amount ?? 0;
  return (
    <section class="p-4 lg:p-8 flex flex-col items-center gap-6">
      <header class="rise flex flex-col items-center gap-2 text-center print:hidden">
        <span class="avatar-disc w-16 h-16 pop" style={{ background: "linear-gradient(180deg, #34c759, #248a3d)" }}>
          <Icon name="check" size={36} />
        </span>
        <h1 class="text-3xl">{onCredit > 0 ? "Venta a crédito registrada" : "Venta registrada"}</h1>
        {change > 0 ? (
          <p class="flex items-baseline gap-2"><span class="label-2">Cambio</span><span class="display text-success">{formatMoney(change, currency)}</span></p>
        ) : (
          <p class="label-2 num">Total {formatMoney(pricing.total, currency)}</p>
        )}
        <p class="text-sm label-2 flex items-center gap-2">
          {result
            ? <><Icon name="checkCircle" size={16} class="text-success" /> Folio {result.pos_reference ?? result.name}</>
            : <><Icon name="cloudOff" size={16} /> Guardada en esta caja; se enviará sola.</>}
        </p>
      </header>
      <article class="receipt bg-white text-black font-mono text-sm p-4 w-[80mm] max-w-full rounded-[12px] shadow-lg print:shadow-none print:rounded-none rise" style={{ animationDelay: "100ms" }}>
        <header class="text-center">
          <div class="font-bold text-base">{company.name}</div>
          {company.vat && <div>RFC: {company.vat}</div>}
          {company.fiscal_regime && <div>{company.fiscal_regime}</div>}
          {[company.street, company.city, company.zip].filter(Boolean).length > 0 && (
            <div>{[company.street, company.city, company.zip].filter(Boolean).join(", ")}</div>
          )}
          {company.phone && <div>Tel. {company.phone}</div>}
          {register.receipt_header && <div class="whitespace-pre-line mt-1">{register.receipt_header}</div>}
        </header>
        <hr class="my-2 border-dashed border-black" />
        <div class="text-center font-bold">{onCredit > 0 ? "VENTA A CRÉDITO" : "TICKET DE VENTA"}</div>
        <div>{formatDateTime(order.created_at ?? Date.now())}</div>
        <div>{register.name}{employee ? ` · ${employee.name}` : ""}</div>
        <div>Folio: {result?.pos_reference ?? result?.name ?? "pendiente (sin internet)"}</div>
        <hr class="my-2 border-dashed border-black" />
        {pricing.lines.map((line) => (
          <div key={line.lineUuid}>
            <div>{line.name}{line.wholesale && " (mayoreo)"}</div>
            <div class="flex justify-between">
              <span>{qtyLabel(line.qty, line.unit)} x {formatMoney(line.displayUnit, currency)}</span>
              <span>{formatMoney(line.total, currency)}</span>
            </div>
          </div>
        ))}
        <hr class="my-2 border-dashed border-black" />
        <div class="flex justify-between"><span>Subtotal</span><span>{formatMoney(pricing.total - tax, currency)}</span></div>
        <div class="flex justify-between"><span>Impuestos</span><span>{formatMoney(tax, currency)}</span></div>
        <div class="flex justify-between font-bold text-base"><span>TOTAL</span><span>{formatMoney(pricing.total, currency)}</span></div>
        {payments.map((payment, index) => (
          <div key={index} class="flex justify-between"><span>{payment.method.name}</span><span>{formatMoney(payment.amount, currency)}</span></div>
        ))}
        {change > 0 && <div class="flex justify-between"><span>Cambio</span><span>{formatMoney(change, currency)}</span></div>}
        {onCredit > 0 && credit && (
          <div class="border-t border-black mt-2 pt-2">
            <div class="flex justify-between"><span>Cliente</span><span class="font-bold">{credit.name}</span></div>
            <div class="flex justify-between"><span>Saldo anterior</span><span>{formatMoney(previous, currency)}</span></div>
            <div class="flex justify-between"><span>Esta compra a crédito</span><span>{formatMoney(onCredit, currency)}</span></div>
            <div class="flex justify-between font-bold"><span>Saldo nuevo</span><span>{formatMoney(previous + onCredit, currency)}</span></div>
            <div class="text-center pt-8">
              <div class="border-t border-black mx-4" />
              <div class="text-xs">Firma del cliente</div>
            </div>
          </div>
        )}
        <hr class="my-2 border-dashed border-black" />
        {company.receipt_legend && <div class="text-center text-xs">{company.receipt_legend}</div>}
        {register.receipt_footer && <div class="text-center whitespace-pre-line">{register.receipt_footer}</div>}
        <div class="text-center text-xs mt-1">{order.uuid.slice(0, 8)}</div>
      </article>
      <div class="flex flex-wrap justify-center gap-4 print:hidden w-full max-w-md">
        <button class="btn btn-xl flex-1 bg-base-100 border-[0.5px] border-[var(--glass-border)]" onClick={() => window.print()}><Icon name="printer" size={20} /> Imprimir</button>
        <button class="btn btn-primary btn-xl flex-1" autofocus onClick={onNext}><Icon name="plus" size={20} /> Nueva venta</button>
      </div>
    </section>
  );
}
