import { useEffect, useRef, useState } from "preact/hooks";

import type { Currency } from "../api/types";
import { formatMoney } from "../lib/money";
import { MAX_QTY, WEIGHT_DECIMALS } from "../sale/cart";
import { round } from "../sale/money";

interface Weighable {
  name: string;
  list_price: number;
  uom?: { name: string };
}

/**
 * Ask for the weight of a product sold by weight: the cashier reads it from
 * the scale (labels from a printing scale skip this: the weight is in the
 * barcode). Shows price per kilo and the amount, as the law asks.
 */
export function WeighDialog({ product, initial, currency, onDone, onCancel }: {
  product: Weighable;
  initial?: number;
  currency: Currency;
  onDone: (qty: number) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(initial ? initial.toFixed(WEIGHT_DECIMALS) : "");
  const input = useRef<HTMLInputElement>(null);
  const unit = product.uom?.name ?? "kg";
  const qty = Number(text.replace(",", "."));
  const valid = Number.isFinite(qty) && qty > 0 && qty <= MAX_QTY;
  const kilos = valid ? Math.round(qty * 10 ** WEIGHT_DECIMALS) / 10 ** WEIGHT_DECIMALS : 0;

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);

  return (
    <dialog class="modal modal-open" aria-label={`Peso de ${product.name}`}>
      <form class="modal-box flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (kilos > 0) onDone(kilos); }}>
        <h3 class="text-lg font-bold">{product.name}</h3>
        <div class="opacity-70">{formatMoney(product.list_price, currency)} / {unit}</div>
        <label class="input input-lg w-full">
          <input ref={input} type="text" inputMode="decimal" autocomplete="off" placeholder="0.000" value={text}
            onInput={(e) => setText(e.currentTarget.value)}
            onKeyDown={(e) => { if (e.key === "Escape") onCancel(); }} />
          <span class="opacity-70">{unit}</span>
        </label>
        <div class="text-2xl font-bold text-right">{formatMoney(round(product.list_price * kilos), currency)}</div>
        <div class="modal-action">
          <button type="button" class="btn" onClick={onCancel}>Cancelar</button>
          <button class="btn btn-primary" disabled={kilos <= 0}>Agregar</button>
        </div>
      </form>
    </dialog>
  );
}
