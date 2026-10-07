import { useEffect, useState } from "preact/hooks";

import type { CashMove } from "../api/types";
import { formatDateTime, formatTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { Banner, EmptyState, Field, PageHeader, Section, Segmented, SLIP, SlipActions, Stat, SuccessHeader } from "../ui/Page";
import { explain } from "./SetupScreen";

/**
 * Cash put in or taken out of the drawer (as the Odoo POS "Entrada/Salida de
 * efectivo"). A manager puts cash in and takes it out, a cashier only takes
 * it out; always with a checked PIN and the network. The closing
 * then expects the drawer to hold that much more or less, and each move
 * prints a voucher to sign.
 */
export function CashMoveScreen({ onClose }: { onClose: () => void }) {
  const { client, setup, online, registerState, employee } = usePos();
  // A cashier may only take cash out (where the register allows it); a manager also puts it in.
  const canPutIn = !registerState?.employee_login || employee?.role === "manager";
  // Quick reasons are the store's own (Point of Sale settings), not the app's.
  const reasons = registerState?.options?.cash_reasons ?? { in: [], out: [] };
  const [moves, setMoves] = useState<CashMove[]>([]);
  const [kind, setKind] = useState<"in" | "out">("out");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [voucher, setVoucher] = useState<CashMove | null>(null);
  // One id per move: a retry after a dropped connection is the same move.
  const [uuid, setUuid] = useState(() => crypto.randomUUID());
  const money = (n: number) => formatMoney(n, setup.store.currency);
  const sessionId = registerState?.session?.id;

  async function load() {
    try {
      setMoves((await client.cashMoves(setup.register.id)).items);
    } catch (err) {
      setError(explain(err));
    }
  }

  useEffect(() => {
    if (online) void load();
  }, [online]);

  async function submit(event: Event) {
    event.preventDefault();
    setError(null);
    const value = Number(amount.replace(",", "."));
    if (!Number.isFinite(value) || value <= 0) return setError("Escribe un importe mayor que cero.");
    if (!reason.trim()) return setError("Escribe el motivo.");
    if (!sessionId) return setError("La caja no tiene una sesión abierta.");
    setBusy(true);
    try {
      const move = await client.cashMove(setup.register.id, {
        session_id: sessionId, uuid, type: kind, amount: Math.round(value * 100) / 100, reason: reason.trim(),
      });
      setVoucher(move);
      setAmount("");
      setReason("");
      setUuid(crypto.randomUUID());
      void load();
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  if (voucher) return <CashMoveVoucher move={voucher} onDone={() => setVoucher(null)} />;

  const total = (type: "in" | "out") => moves.filter((m) => m.type === type).reduce((t, m) => t + m.amount, 0);
  return (
    <section class="p-4 lg:p-8 max-w-2xl mx-auto flex flex-col gap-6 rise">
      <PageHeader title="Entradas y salidas" subtitle="Efectivo del cajón" icon="cash" back={onClose} />
      {!online && <Banner tone="warn">Meter o sacar efectivo necesita internet.</Banner>}
      <form class="surface p-4 flex flex-col gap-4" onSubmit={(e) => void submit(e)}>
        {canPutIn && (
          <Segmented label="Tipo de movimiento" value={kind} onChange={(value) => { setKind(value); setReason(""); }}
            options={[{ value: "out", label: "Salida (sacar)" }, { value: "in", label: "Entrada (meter)" }]} />
        )}
        <Field label={kind === "out" ? "¿Cuánto sale?" : "¿Cuánto entra?"}>
          <label class="input input-lg w-full !h-16 text-3xl num">
            <span class="label-2">$</span>
            <input type="text" inputMode="decimal" autocomplete="off" placeholder="0.00" value={amount} aria-label="Importe"
              onInput={(e) => setAmount(e.currentTarget.value)} />
          </label>
        </Field>
        <Field label="Motivo">
          {reasons[kind].length > 0 && (
            <div class="flex flex-wrap gap-2">
              {reasons[kind].map((text) => (
                <button key={text} type="button" class="chip" aria-selected={reason === text} onClick={() => setReason(text)}>{text}</button>
              ))}
            </div>
          )}
          <input class="input w-full" type="text" maxLength={200} placeholder="Motivo (p. ej. pago a Coca-Cola)"
            value={reason} onInput={(e) => setReason(e.currentTarget.value)} />
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
        <button class="btn btn-primary btn-xl" disabled={busy || !online}>
          {busy ? <span class="loading loading-spinner" /> : kind === "out" ? "Registrar salida" : "Registrar entrada"}
        </button>
        {employee && <p class="text-xs label-2 text-center">Queda registrado a nombre de {employee.name} y aparece en el corte.</p>}
      </form>

      {moves.length > 0 && (
        <div class="grid gap-4 grid-cols-2">
          <Stat label="Entradas" value={money(total("in"))} tone="good" />
          <Stat label="Salidas" value={money(total("out"))} tone="warn" />
        </div>
      )}
      {moves.length ? (
        <Section title="Movimientos de este turno">
          {moves.map((move) => (
            <div key={move.id} class="row !py-3">
              <span class="method-icon !w-9 !h-9 !rounded-[10px] shrink-0" aria-hidden="true"
                style={{ background: move.type === "out" ? "linear-gradient(180deg, #ffb340, #ff9500)" : "linear-gradient(180deg, #34c759, #248a3d)" }}>
                <Icon name={move.type === "out" ? "arrowUp" : "arrowDown"} size={18} />
              </span>
              <div class="flex-1 min-w-0">
                <div class="font-medium truncate">{move.name}</div>
                <div class="text-xs label-2">{move.employee ?? ""}{move.date && ` · ${formatTime(move.date)}`}</div>
              </div>
              <span class={`font-semibold num ${move.type === "out" ? "pill-warn" : "text-success"}`}>
                {move.type === "out" ? "−" : "+"}{money(move.amount)}
              </span>
              <button class="btn btn-ghost btn-sm btn-square text-primary" aria-label="Reimprimir comprobante" title="Reimprimir"
                onClick={() => setVoucher(move)}><Icon name="printer" size={18} /></button>
            </div>
          ))}
        </Section>
      ) : online && <EmptyState icon="cash" title="Sin movimientos en este turno" />}
    </section>
  );
}

function CashMoveVoucher({ move, onDone }: { move: CashMove; onDone: () => void }) {
  const { setup } = usePos();
  const money = (n: number) => formatMoney(n, setup.store.currency);
  return (
    <section class="p-4 lg:p-8 flex flex-col items-center gap-6">
      <SuccessHeader title={move.type === "out" ? "Salida registrada" : "Entrada registrada"} tone={move.type === "out" ? "warn" : "good"}>
        <span class="num">{money(move.amount)} · {move.name}</span>
      </SuccessHeader>
      <article class={SLIP} style={{ animationDelay: "100ms" }}>
        <div class="text-center font-bold text-base">{move.type === "out" ? "SALIDA DE EFECTIVO" : "ENTRADA DE EFECTIVO"}</div>
        <div class="text-center">{setup.store.company.name}</div>
        <hr class="my-2 border-dashed border-black" />
        <div>{setup.register.name}</div>
        {move.date && <div>{formatDateTime(move.date)}</div>}
        {move.employee && <div>Registró: {move.employee}</div>}
        <div class="mt-1">{move.name}</div>
        <hr class="my-2 border-dashed border-black" />
        <div class="flex justify-between font-bold text-base"><span>IMPORTE</span><span>{money(move.amount)}</span></div>
        <div class="text-center pt-8">
          <div class="border-t border-black mx-4" />
          <div class="text-xs">{move.type === "out" ? "Firma de quien recibe" : "Firma de quien entrega"}</div>
        </div>
      </article>
      <SlipActions onDone={onDone} />
    </section>
  );
}
