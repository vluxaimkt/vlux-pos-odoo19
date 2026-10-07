import { useEffect, useState } from "preact/hooks";

import { ApiError } from "../api/client";
import type { ClosingSummary, RegisterState } from "../api/types";
import { formatDateTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { type ClosingCount, closingRequest, countClosing } from "../sale/closing";
import { usePos } from "../state";
import { explain } from "./SetupScreen";
import { Icon } from "../ui/Icon";
import { Banner, EmptyState, Field, Loading, PageHeader, Section, Stat } from "../ui/Page";

type Done = { summary: ClosingSummary; count: ClosingCount; closedAt: Date; notes: string };

/**
 * Corte de caja. The server has the last word: it books the differences and
 * applies the register's limit (above it only a manager may close). Sales
 * still waiting in this register's queue would be missing from the expected
 * amounts, so the closing waits until they are sent.
 */
export function CloseScreen({ onClosed, onCancel, onQueue }: {
  onClosed: (state: RegisterState) => void;
  onCancel: () => void;
  onQueue: () => void;
}) {
  const { db, client, setup, employee, online, setEmployee, flushNow } = usePos();
  const [summary, setSummary] = useState<ClosingSummary | null>(null);
  const [pending, setPending] = useState(0);
  const [attention, setAttention] = useState(0);
  const [cash, setCash] = useState("");
  const [counted, setCounted] = useState<Record<number, string>>({});
  const [notes, setNotes] = useState("");
  const [acceptAttention, setAcceptAttention] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Done | null>(null);
  const [finalState, setFinalState] = useState<RegisterState | null>(null);
  const currency = setup.store.currency;

  async function load() {
    setError(null);
    await flushNow();
    setPending(await db.outbox.where("status").equals("pending").count());
    setAttention(await db.outbox.where("status").equals("attention").count());
    try {
      setSummary(await client.closingSummary(setup.register.id));
    } catch (err) {
      setError(explain(err));
    }
  }

  useEffect(() => {
    if (online) void load();
  }, [online]);

  if (done && finalState) {
    return <ClosingReport done={done} onFinish={() => onClosed(finalState)} />;
  }
  if (!online) {
    return (
      <section class="p-4 lg:p-8 max-w-2xl mx-auto flex flex-col gap-6">
        <PageHeader title="Corte de caja" icon="calculator" back={onCancel} />
        <EmptyState icon="cloudOff" title="El corte de caja necesita internet" hint="Vuelve a intentarlo cuando regrese la conexión." />
      </section>
    );
  }
  if (!summary) {
    return (
      <section class="p-4 lg:p-8 max-w-2xl mx-auto flex flex-col gap-6">
        <PageHeader title="Corte de caja" icon="calculator" back={onCancel} />
        {error ? <Banner tone="error">{error}</Banner> : <Loading />}
      </section>
    );
  }

  const countedMap = new Map(Object.entries(counted).filter(([, v]) => v !== "").map(([k, v]) => [Number(k), Number(v)]));
  const count = countClosing(summary, Number(cash || 0), countedMap);
  const blocked = pending > 0 || (attention > 0 && !acceptAttention);
  const money = (value: number) => formatMoney(value, currency);

  async function close(event: Event) {
    event.preventDefault();
    if (!summary || blocked) return;
    setBusy(true);
    setError(null);
    try {
      const state = await client.closeSession(setup.register.id, closingRequest(summary, count, employee?.id ?? null, notes));
      setFinalState(state);
      setDone({ summary, count, closedAt: new Date(), notes });
    } catch (err) {
      setError(err instanceof ApiError && err.code === "CLOSING_REFUSED"
        ? `${err.message} La caja sigue abierta.`
        : explain(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form class="p-4 lg:p-8 max-w-2xl mx-auto flex flex-col gap-6 rise" onSubmit={close}>
      <PageHeader title="Corte de caja" subtitle={`${setup.register.name} · ${summary.session.name}`} icon="calculator" back={onCancel} />

      {pending > 0 && (
        <Banner tone="warn" actions={<button type="button" class="btn btn-sm" onClick={() => void load()}>Enviar y volver a calcular</button>}>
          Hay {pending} venta(s) de esta caja sin enviar: el corte no las contaría. Envíalas primero.
        </Banner>
      )}
      {attention > 0 && (
        <Banner tone="error" actions={
          <>
            <button type="button" class="btn btn-sm" onClick={onQueue}>Ver ventas por revisar</button>
            <label class="flex items-center gap-2 cursor-pointer text-sm">
              <input type="checkbox" class="checkbox checkbox-sm" checked={acceptAttention}
                onChange={(event) => setAcceptAttention(event.currentTarget.checked)} />
              Cerrar de todos modos
            </label>
          </>
        }>
          Hay {attention} venta(s) rechazada(s) por el servidor: no están en el corte. Revísalas con el encargado.
        </Banner>
      )}

      <div class="grid gap-4 sm:grid-cols-2">
        <Stat label="Ventas" value={money(summary.orders.amount)} note={`${summary.orders.count} ${summary.orders.count === 1 ? "venta" : "ventas"}`} />
        {summary.cash && (
          <Stat label="Efectivo esperado" value={money(summary.cash.expected)}
            note={<>
              Inicial {money(summary.cash.opening)} + ventas {money(summary.cash.sales)}
              {summary.cash.moves.length > 0 && ` ${movesTotal(summary.cash.moves) < 0 ? "−" : "+"} entradas/salidas ${money(Math.abs(movesTotal(summary.cash.moves)))}`}
            </>} />
        )}
      </div>

      {summary.credit && <CreditDetail credit={summary.credit} money={money} />}

      {count.cash && (
        <Field label="Efectivo contado en el cajón">
          <input class="input input-lg w-full text-2xl num" type="number" inputMode="decimal" min="0" step="0.01" required
            placeholder={money(summary.cash?.expected ?? 0)}
            value={cash} onInput={(event) => setCash(event.currentTarget.value)} />
          {cash !== "" && <Difference value={count.cash.difference} money={money} />}
        </Field>
      )}
      {count.others.map((line) => (
        <Field key={line.paymentMethodId} label={`${line.name} según terminal · esperado ${money(line.expected)}`}>
          <input class="input w-full num" type="number" inputMode="decimal" min="0" step="0.01"
            placeholder={String(line.expected)} value={counted[line.paymentMethodId] ?? ""}
            onInput={(event) => setCounted({ ...counted, [line.paymentMethodId]: event.currentTarget.value })} />
          <Difference value={line.difference} money={money} />
        </Field>
      ))}
      <Field label="Notas (opcional)">
        <textarea class="textarea w-full" maxLength={1000} value={notes}
          onInput={(event) => setNotes(event.currentTarget.value)} />
      </Field>

      {!count.withinLimit && cash !== "" && (
        <Banner tone="warn" actions={employee && employee.role !== "manager" && (
          <button type="button" class="btn btn-sm whitespace-nowrap" onClick={() => setEmployee(null)}>Entrar como encargado</button>
        )}>
          La diferencia ({money(count.largest)}) supera la permitida ({money(summary.max_difference ?? 0)}).
          Vuelve a contar; si es correcta, sólo un encargado puede cerrar.
        </Banner>
      )}
      {error && <Banner tone="error">{error}</Banner>}

      <button class="btn btn-primary btn-xl" type="submit" disabled={busy || blocked || (!!count.cash && cash === "")}>
        {busy ? <span class="loading loading-spinner" /> : "Cerrar caja"}
      </button>
    </form>
  );
}

type CreditSection = NonNullable<ClosingSummary["credit"]>;

/** Each sale on credit and each abono, by customer: the net of the day hides both. */
function CreditDetail({ credit, money }: { credit: CreditSection; money: (n: number) => string }) {
  if (!credit.sales.length && !credit.abonos.length) return null;
  return (
    <div class="grid gap-4 sm:grid-cols-2">
      <Section title="Fiado" footer="No entra al cajón.">
        {credit.sales.map((row) => (
          <div key={row.reference} class="row !min-h-12 text-sm">
            <span class="flex-1 min-w-0 truncate">
              {row.customer} <span class="label-2">· {row.reference}</span>
              {row.flagged && <span title="Revisar" aria-label="Revisar"><Icon name="warning" size={16} class="inline ml-2 text-warning align-[-2px]" /></span>}
            </span>
            <span class="num">{money(row.amount)}</span>
          </div>
        ))}
        {!credit.sales.length && <div class="row !min-h-12 text-sm label-2">Nada fiado.</div>}
        <div class="row !min-h-12 font-semibold"><span class="flex-1">Total fiado</span><span class="num">{money(credit.total_sales)}</span></div>
      </Section>
      <Section title="Abonos recibidos" footer="Los abonos en efectivo ya están en el efectivo esperado.">
        {credit.abonos.map((row) => (
          <div key={row.reference} class="row !min-h-12 text-sm">
            <span class="flex-1 min-w-0 truncate">{row.customer} <span class="label-2">· {row.method}</span></span>
            <span class="num">{money(row.amount)}</span>
          </div>
        ))}
        {!credit.abonos.length && <div class="row !min-h-12 text-sm label-2">Sin abonos.</div>}
        <div class="row !min-h-12 font-semibold"><span class="flex-1">Total abonos</span><span class="num">{money(credit.total_abonos)}</span></div>
      </Section>
    </div>
  );
}

function Difference({ value, money }: { value: number; money: (n: number) => string }) {
  if (value === 0) {
    return <span class="text-sm font-medium text-success flex items-center gap-2 px-1"><Icon name="checkCircle" size={16} /> Cuadra</span>;
  }
  return (
    <span class={`text-sm font-medium flex items-center gap-2 px-1 num ${value < 0 ? "text-danger" : "text-warning"}`}>
      <Icon name="warning" size={16} /> {value < 0 ? "Falta" : "Sobra"} {money(Math.abs(value))}
    </span>
  );
}

function movesTotal(moves: { amount: number }[]): number {
  return Math.round(moves.reduce((total, move) => total + move.amount, 0) * 100) / 100;
}

/** The printable closing slip (80 mm), from the counts the server accepted. */
function ClosingReport({ done, onFinish }: { done: Done; onFinish: () => void }) {
  const { setup, employee } = usePos();
  const currency = setup.store.currency;
  const lines = [done.count.cash, ...done.count.others].filter((line) => !!line);
  return (
    <section class="p-4 lg:p-8 flex flex-col items-center gap-6">
      <header class="rise flex flex-col items-center gap-2 text-center print:hidden">
        <span class="avatar-disc w-16 h-16 pop" style={{ background: "linear-gradient(180deg, #34c759, #248a3d)" }}>
          <Icon name="check" size={36} />
        </span>
        <h1 class="text-3xl">Caja cerrada</h1>
        <p class="label-2">{setup.register.name} · {done.summary.session.name}</p>
      </header>
      <article class="receipt bg-white text-black font-mono text-sm p-4 w-[80mm] max-w-full rounded-[12px] shadow-lg print:shadow-none print:rounded-none rise" style={{ animationDelay: "100ms" }}>
        <div class="text-center font-bold text-base">CORTE DE CAJA</div>
        <div class="text-center">{setup.store.company.name}</div>
        <hr class="my-2 border-dashed border-black" />
        <div>{setup.register.name} · {done.summary.session.name}</div>
        {done.summary.session.opened_at && (
          <div>Apertura: {formatDateTime(done.summary.session.opened_at)}</div>
        )}
        <div>Cierre: {formatDateTime(done.closedAt)}</div>
        {employee && <div>Cerró: {employee.name}</div>}
        <hr class="my-2 border-dashed border-black" />
        <div class="flex justify-between"><span>Ventas ({done.summary.orders.count})</span><span>{formatMoney(done.summary.orders.amount, currency)}</span></div>
        {done.summary.cash && (
          <div class="flex justify-between"><span>Fondo inicial</span><span>{formatMoney(done.summary.cash.opening, currency)}</span></div>
        )}
        {done.summary.cash && done.summary.cash.moves.length > 0 && (
          <>
            <div class="font-bold mt-1">ENTRADAS Y SALIDAS</div>
            {done.summary.cash.moves.map((move, index) => (
              <div key={index}>
                <div>{move.name}{move.employee ? ` (${move.employee})` : ""}</div>
                <div class="flex justify-end"><span>{formatMoney(move.amount, currency)}</span></div>
              </div>
            ))}
          </>
        )}
        {lines.map((line) => (
          <div key={line.paymentMethodId} class="mt-1">
            <div class="font-bold">{line.name}</div>
            <div class="flex justify-between"><span>Esperado</span><span>{formatMoney(line.expected, currency)}</span></div>
            <div class="flex justify-between"><span>Contado</span><span>{formatMoney(line.counted, currency)}</span></div>
            <div class="flex justify-between"><span>Diferencia</span><span>{formatMoney(line.difference, currency)}</span></div>
          </div>
        ))}
        {done.summary.credit && (done.summary.credit.sales.length > 0 || done.summary.credit.abonos.length > 0) && (
          <>
            <hr class="my-2 border-dashed border-black" />
            <div class="font-bold">VENTAS A CRÉDITO (FIADO)</div>
            {done.summary.credit.sales.map((row) => (
              <div key={row.reference}>
                <div>{row.customer}</div>
                <div class="flex justify-between"><span class="text-xs">{row.reference}</span><span>{formatMoney(row.amount, currency)}</span></div>
              </div>
            ))}
            {!done.summary.credit.sales.length && <div>Sin ventas a crédito</div>}
            <div class="flex justify-between font-bold"><span>Total fiado</span><span>{formatMoney(done.summary.credit.total_sales, currency)}</span></div>
            <div class="font-bold mt-2">ABONOS RECIBIDOS</div>
            {done.summary.credit.abonos.map((row) => (
              <div key={row.reference}>
                <div>{row.customer}</div>
                <div class="flex justify-between"><span class="text-xs">{row.method} · {row.reference}</span><span>{formatMoney(row.amount, currency)}</span></div>
              </div>
            ))}
            {!done.summary.credit.abonos.length && <div>Sin abonos</div>}
            <div class="flex justify-between font-bold"><span>Total abonos</span><span>{formatMoney(done.summary.credit.total_abonos, currency)}</span></div>
          </>
        )}
        {done.notes.trim() && <><hr class="my-2 border-dashed border-black" /><div class="whitespace-pre-line">{done.notes.trim()}</div></>}
        <hr class="my-2 border-dashed border-black" />
        <div class="text-center text-xs">Firma: ______________________</div>
      </article>
      <div class="flex flex-wrap justify-center gap-4 print:hidden w-full max-w-md">
        <button class="btn btn-xl flex-1 bg-base-100 border-[0.5px] border-[var(--glass-border)]" onClick={() => window.print()}>
          <Icon name="printer" size={20} /> Imprimir corte
        </button>
        <button class="btn btn-primary btn-xl flex-1" autofocus onClick={onFinish}>Terminar</button>
      </div>
    </section>
  );
}
