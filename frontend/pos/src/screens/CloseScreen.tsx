import { useEffect, useState } from "preact/hooks";

import { ApiError } from "../api/client";
import type { ClosingSummary, RegisterState } from "../api/types";
import { formatMoney } from "../lib/money";
import { type ClosingCount, closingRequest, countClosing } from "../sale/closing";
import { usePos } from "../state";
import { explain } from "./SetupScreen";

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
  if (!online) return <div role="alert" class="alert alert-warning m-4">El corte de caja necesita internet.</div>;
  if (!summary) {
    return (
      <div class="p-4 flex flex-col gap-3 items-center">
        {error ? <div role="alert" class="alert alert-error">{error}</div> : <span class="loading loading-spinner loading-lg" />}
        <button class="btn btn-ghost" onClick={onCancel}>Volver</button>
      </div>
    );
  }

  const countedMap = new Map(Object.entries(counted).filter(([, v]) => v !== "").map(([k, v]) => [Number(k), Number(v)]));
  const count = countClosing(summary, Number(cash || 0), countedMap);
  const blocked = pending > 0 || (attention > 0 && !acceptAttention);

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
    <form class="p-4 max-w-2xl mx-auto flex flex-col gap-4" onSubmit={close}>
      <div class="flex items-center gap-2">
        <h2 class="text-xl flex-1">Corte de caja · {summary.session.name}</h2>
        <button type="button" class="btn btn-ghost btn-sm" onClick={onCancel}>Volver a vender</button>
      </div>

      {pending > 0 && (
        <div role="alert" class="alert alert-warning">
          <div class="flex flex-col gap-2">
            <span>Hay {pending} venta(s) de esta caja sin enviar: el corte no las contaría. Envíalas primero.</span>
            <button type="button" class="btn btn-sm w-fit" onClick={() => void load()}>Enviar y volver a calcular</button>
          </div>
        </div>
      )}
      {attention > 0 && (
        <div role="alert" class="alert alert-error">
          <div class="flex flex-col gap-2">
          <span>Hay {attention} venta(s) rechazada(s) por el servidor: no están en el corte. Revísalas con el encargado.</span>
          <div class="flex flex-wrap gap-2 items-center">
            <button type="button" class="btn btn-sm" onClick={onQueue}>Ver ventas por revisar</button>
            <label class="label cursor-pointer gap-2">
              <input type="checkbox" class="checkbox checkbox-sm" checked={acceptAttention}
                onChange={(event) => setAcceptAttention(event.currentTarget.checked)} />
              <span>Cerrar de todos modos</span>
            </label>
          </div>
          </div>
        </div>
      )}

      <div class="stats shadow bg-base-100">
        <div class="stat">
          <div class="stat-title">Ventas</div>
          <div class="stat-value text-2xl">{summary.orders.count}</div>
          <div class="stat-desc">{formatMoney(summary.orders.amount, currency)}</div>
        </div>
        {summary.cash && (
          <div class="stat">
            <div class="stat-title">Efectivo esperado</div>
            <div class="stat-value text-2xl">{formatMoney(summary.cash.expected, currency)}</div>
            <div class="stat-desc">
              Inicial {formatMoney(summary.cash.opening, currency)} + ventas {formatMoney(summary.cash.sales, currency)}
              {summary.cash.moves.length > 0 && ` + movimientos ${formatMoney(summary.cash.moves.reduce((t, m) => t + m.amount, 0), currency)}`}
            </div>
          </div>
        )}
      </div>

      {count.cash && (
        <fieldset class="fieldset">
          <legend class="fieldset-legend">Efectivo contado en el cajón</legend>
          <input class="input input-lg w-full" type="number" inputMode="decimal" min="0" step="0.01" required
            value={cash} onInput={(event) => setCash(event.currentTarget.value)} />
          {cash !== "" && <Difference value={count.cash.difference} currency={currency} />}
        </fieldset>
      )}
      {count.others.map((line) => (
        <fieldset key={line.paymentMethodId} class="fieldset">
          <legend class="fieldset-legend">{line.name} (según terminal): esperado {formatMoney(line.expected, currency)}</legend>
          <input class="input w-full" type="number" inputMode="decimal" min="0" step="0.01"
            placeholder={String(line.expected)} value={counted[line.paymentMethodId] ?? ""}
            onInput={(event) => setCounted({ ...counted, [line.paymentMethodId]: event.currentTarget.value })} />
          <Difference value={line.difference} currency={currency} />
        </fieldset>
      ))}
      <fieldset class="fieldset">
        <legend class="fieldset-legend">Notas (opcional)</legend>
        <textarea class="textarea w-full" maxLength={1000} value={notes}
          onInput={(event) => setNotes(event.currentTarget.value)} />
      </fieldset>

      {!count.withinLimit && cash !== "" && (
        <div role="alert" class="alert alert-warning">
          <div class="flex flex-col gap-2">
          <span>
            La diferencia ({formatMoney(count.largest, currency)}) supera la permitida
            ({formatMoney(summary.max_difference ?? 0, currency)}). Vuelve a contar; si es correcta, sólo un encargado puede cerrar.
          </span>
          {employee && employee.role !== "manager" && (
            <button type="button" class="btn btn-sm w-fit whitespace-nowrap" onClick={() => setEmployee(null)}>Entrar como encargado</button>
          )}
          </div>
        </div>
      )}
      {error && <div role="alert" class="alert alert-error">{error}</div>}

      <button class="btn btn-primary btn-lg" type="submit" disabled={busy || blocked || (!!count.cash && cash === "")}>
        {busy ? <span class="loading loading-spinner" /> : "Cerrar caja"}
      </button>
    </form>
  );
}

function Difference({ value, currency }: { value: number; currency: Parameters<typeof formatMoney>[1] }) {
  if (value === 0) return <span class="label text-success">Cuadra</span>;
  return (
    <span class={`label ${value < 0 ? "text-error" : "text-warning"}`}>
      {value < 0 ? "Falta" : "Sobra"} {formatMoney(Math.abs(value), currency)}
    </span>
  );
}

/** The printable closing slip (80 mm), from the counts the server accepted. */
function ClosingReport({ done, onFinish }: { done: Done; onFinish: () => void }) {
  const { setup, employee } = usePos();
  const currency = setup.store.currency;
  const lines = [done.count.cash, ...done.count.others].filter((line) => !!line);
  return (
    <section class="p-4 flex flex-col items-center gap-4">
      <article class="receipt bg-white text-black font-mono text-sm p-4 w-[80mm] max-w-full shadow print:shadow-none">
        <div class="text-center font-bold text-base">CORTE DE CAJA</div>
        <div class="text-center">{setup.store.company.name}</div>
        <hr class="my-2 border-dashed border-black" />
        <div>{setup.register.name} · {done.summary.session.name}</div>
        {done.summary.session.opened_at && (
          <div>Apertura: {new Date(done.summary.session.opened_at).toLocaleString("es-MX")}</div>
        )}
        <div>Cierre: {done.closedAt.toLocaleString("es-MX")}</div>
        {employee && <div>Cerró: {employee.name}</div>}
        <hr class="my-2 border-dashed border-black" />
        <div class="flex justify-between"><span>Ventas ({done.summary.orders.count})</span><span>{formatMoney(done.summary.orders.amount, currency)}</span></div>
        {done.summary.cash && (
          <div class="flex justify-between"><span>Fondo inicial</span><span>{formatMoney(done.summary.cash.opening, currency)}</span></div>
        )}
        {lines.map((line) => (
          <div key={line.paymentMethodId} class="mt-1">
            <div class="font-bold">{line.name}</div>
            <div class="flex justify-between"><span>Esperado</span><span>{formatMoney(line.expected, currency)}</span></div>
            <div class="flex justify-between"><span>Contado</span><span>{formatMoney(line.counted, currency)}</span></div>
            <div class="flex justify-between"><span>Diferencia</span><span>{formatMoney(line.difference, currency)}</span></div>
          </div>
        ))}
        {done.notes.trim() && <><hr class="my-2 border-dashed border-black" /><div class="whitespace-pre-line">{done.notes.trim()}</div></>}
        <hr class="my-2 border-dashed border-black" />
        <div class="text-center text-xs">Firma: ______________________</div>
      </article>
      <div class="flex gap-2 print:hidden">
        <button class="btn btn-lg" onClick={() => window.print()}>Imprimir corte</button>
        <button class="btn btn-primary btn-lg" onClick={onFinish}>Terminar</button>
      </div>
    </section>
  );
}
