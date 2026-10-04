import { useEffect, useState } from "preact/hooks";

import type { CashMove } from "../api/types";
import { formatMoney } from "../lib/money";
import { usePos } from "../state";
import { explain } from "./SetupScreen";

const REASONS = {
  in: ["Cambio (morralla)", "Fondo adicional"],
  out: ["Pago a proveedor", "Retiro del dueño", "Gasto de la tienda"],
};

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
    <section class="p-4 max-w-2xl mx-auto flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <h2 class="text-xl flex-1">Entradas y salidas de efectivo</h2>
        <button class="btn btn-ghost btn-sm" onClick={onClose}>Volver a vender</button>
      </div>
      {!online && <div role="alert" class="alert alert-warning">Meter o sacar efectivo necesita internet.</div>}
      <form class="card bg-base-100 shadow" onSubmit={(e) => void submit(e)}>
        <div class="card-body gap-3">
          <div class="join">
            <button type="button" class={`btn join-item ${kind === "out" ? "btn-warning" : "btn-outline"}`}
              onClick={() => { setKind("out"); setReason(""); }}>Salida (sacar)</button>
            {canPutIn && (
              <button type="button" class={`btn join-item ${kind === "in" ? "btn-success" : "btn-outline"}`}
                onClick={() => { setKind("in"); setReason(""); }}>Entrada (meter)</button>
            )}
          </div>
          <label class="input input-lg w-full">
            <span class="opacity-70">$</span>
            <input type="text" inputMode="decimal" autocomplete="off" placeholder="0.00" value={amount}
              onInput={(e) => setAmount(e.currentTarget.value)} />
          </label>
          <div class="flex flex-wrap gap-1">
            {REASONS[kind].map((text) => (
              <button key={text} type="button" class="btn btn-xs" onClick={() => setReason(text)}>{text}</button>
            ))}
          </div>
          <input class="input w-full" type="text" maxLength={200} placeholder="Motivo (p. ej. pago a Coca-Cola)"
            value={reason} onInput={(e) => setReason(e.currentTarget.value)} />
          {error && <div role="alert" class="alert alert-error">{error}</div>}
          <button class="btn btn-primary btn-lg" disabled={busy || !online}>
            {busy ? <span class="loading loading-spinner" /> : kind === "out" ? "Registrar salida" : "Registrar entrada"}
          </button>
          {employee && <p class="text-xs opacity-60">Queda registrado a nombre de {employee.name} y aparece en el corte.</p>}
        </div>
      </form>

      <h3 class="font-semibold">Movimientos de este turno</h3>
      <ul class="list bg-base-100 rounded-box">
        {moves.map((move) => (
          <li key={move.id} class="list-row items-center">
            <div class="list-col-grow">
              <div>{move.name}</div>
              <div class="text-xs opacity-70">
                {move.employee ?? ""}{move.date && ` · ${new Date(move.date).toLocaleTimeString("es-MX")}`}
              </div>
            </div>
            <div class={`font-semibold ${move.type === "out" ? "text-warning" : "text-success"}`}>
              {move.type === "out" ? "−" : "+"}{money(move.amount)}
            </div>
            <button class="btn btn-xs" onClick={() => setVoucher(move)}>Reimprimir</button>
          </li>
        ))}
      </ul>
      {!moves.length && online && <p class="opacity-60">Sin movimientos en este turno.</p>}
      {moves.length > 0 && (
        <p class="text-sm opacity-70">Entradas {money(total("in"))} · Salidas {money(total("out"))}</p>
      )}
    </section>
  );
}

function CashMoveVoucher({ move, onDone }: { move: CashMove; onDone: () => void }) {
  const { setup } = usePos();
  const money = (n: number) => formatMoney(n, setup.store.currency);
  return (
    <section class="p-4 flex flex-col items-center gap-4">
      <article class="receipt bg-white text-black font-mono text-sm p-4 w-[80mm] max-w-full shadow print:shadow-none">
        <div class="text-center font-bold text-base">{move.type === "out" ? "SALIDA DE EFECTIVO" : "ENTRADA DE EFECTIVO"}</div>
        <div class="text-center">{setup.store.company.name}</div>
        <hr class="my-2 border-dashed border-black" />
        <div>{setup.register.name}</div>
        {move.date && <div>{new Date(move.date).toLocaleString("es-MX")}</div>}
        {move.employee && <div>Registró: {move.employee}</div>}
        <div class="mt-1">{move.name}</div>
        <hr class="my-2 border-dashed border-black" />
        <div class="flex justify-between font-bold text-base"><span>IMPORTE</span><span>{money(move.amount)}</span></div>
        <div class="text-center pt-8">
          <div class="border-t border-black mx-4" />
          <div class="text-xs">{move.type === "out" ? "Firma de quien recibe" : "Firma de quien entrega"}</div>
        </div>
      </article>
      <div class="flex gap-2 print:hidden">
        <button class="btn btn-lg" onClick={() => window.print()}>Imprimir</button>
        <button class="btn btn-primary btn-lg" onClick={onDone}>Listo</button>
      </div>
    </section>
  );
}
