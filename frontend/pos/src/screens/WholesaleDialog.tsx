import { useEffect, useRef, useState } from "preact/hooks";

import type { Currency } from "../api/types";
import { formatMoney } from "../lib/money";
import { round } from "../sale/money";
import { Icon } from "../ui/Icon";

/**
 * The wholesale price of one line, typed by the cashier: it changes from
 * customer to customer, so the store keeps no fixed wholesale price. Shows
 * the catalog price for reference and what the line comes to.
 */
export function WholesaleDialog({ name, catalogPrice, current, qty, unit, currency, onDone, onRemove, onCancel }: {
  name: string;
  catalogPrice: number;
  /** The wholesale price already set on the line, if any. */
  current?: number;
  qty: number;
  /** "pieza" or the weight unit ("kg"). */
  unit: string;
  currency: Currency;
  onDone: (price: number) => void;
  onRemove: () => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(current ? String(current) : "");
  const input = useRef<HTMLInputElement>(null);
  const price = Number(text.replace(",", "."));
  const valid = Number.isFinite(price) && price > 0;
  const money = (value: number) => formatMoney(value, currency);

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);

  return (
    <dialog class="modal modal-open" aria-label={`Precio de mayoreo de ${name}`}>
      <form class="modal-box flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); if (valid) onDone(price); }}>
        <div class="flex items-center gap-3">
          <span class="avatar-disc w-12 h-12 shrink-0" style={{ background: "linear-gradient(180deg, #ffb340, #ff9500)" }} aria-hidden="true">
            <Icon name="people" size={24} />
          </span>
          <div class="min-w-0">
            <h3 class="text-xl">Precio de mayoreo</h3>
            <div class="text-sm label-2 truncate">{name}</div>
          </div>
        </div>
        <label class="input input-lg w-full !h-16 text-3xl num">
          <span class="label-2">$</span>
          <input ref={input} type="text" inputMode="decimal" autocomplete="off" placeholder="0.00" value={text}
            aria-label={`Precio de mayoreo por ${unit}`}
            onInput={(e) => setText(e.currentTarget.value)}
            onKeyDown={(e) => { if (e.key === "Escape") onCancel(); }} />
          <span class="label-2 text-xl">/ {unit}</span>
        </label>
        <div class="text-sm label-2 num flex flex-col gap-1">
          <span>Precio normal: {money(catalogPrice)} / {unit}</span>
          {valid && <span>Este renglón: {qty} × {money(price)} = <strong class="text-base-content">{money(round(price * qty))}</strong></span>}
        </div>
        <div class="modal-action mt-0 flex-wrap">
          {current !== undefined && (
            <button type="button" class="btn btn-ghost text-danger mr-auto" onClick={onRemove}>Quitar mayoreo</button>
          )}
          <button type="button" class="btn" onClick={onCancel}>Cancelar</button>
          <button class="btn btn-primary" disabled={!valid}>Aplicar</button>
        </div>
      </form>
    </dialog>
  );
}
