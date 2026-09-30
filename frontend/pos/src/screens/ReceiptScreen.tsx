import { useEffect, useState } from "preact/hooks";

import type { OrderRequest, OrderResult } from "../api/types";
import { formatMoney } from "../lib/money";
import { paymentState } from "../sale/payment";
import type { Pricing } from "../sale/pricing";
import { usePos } from "../state";

/**
 * The ticket. It is printed from what was charged; once the server has the
 * sale, its folio replaces the "pending" mark. Offline, the tax split shown
 * is the register's own calculation (the server's is the one booked).
 */
export function ReceiptScreen({ order, pricing, onNext }: { order: OrderRequest; pricing: Pricing; onNext: () => void }) {
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
  return (
    <section class="p-4 flex flex-col items-center gap-4">
      <article class="receipt bg-white text-black font-mono text-sm p-4 w-[80mm] max-w-full shadow print:shadow-none">
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
        <div>{new Date(order.created_at ?? Date.now()).toLocaleString("es-MX")}</div>
        <div>{register.name}{employee ? ` · ${employee.name}` : ""}</div>
        <div>Folio: {result?.pos_reference ?? result?.name ?? "pendiente (sin internet)"}</div>
        <hr class="my-2 border-dashed border-black" />
        {pricing.lines.map((line) => (
          <div key={line.lineUuid}>
            <div>{line.name}</div>
            <div class="flex justify-between">
              <span>{line.qty} x {formatMoney(line.priceUnit, currency)}</span>
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
        <hr class="my-2 border-dashed border-black" />
        {company.receipt_legend && <div class="text-center text-xs">{company.receipt_legend}</div>}
        {register.receipt_footer && <div class="text-center whitespace-pre-line">{register.receipt_footer}</div>}
        <div class="text-center text-xs mt-1">{order.uuid.slice(0, 8)}</div>
      </article>
      <div class="flex gap-2 print:hidden">
        <button class="btn btn-lg" onClick={() => window.print()}>Imprimir ticket</button>
        <button class="btn btn-primary btn-lg" autofocus onClick={onNext}>Nueva venta</button>
      </div>
      {!result && <p class="text-sm opacity-70 print:hidden">La venta está guardada en esta caja y se enviará sola.</p>}
    </section>
  );
}
