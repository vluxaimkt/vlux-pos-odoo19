import { useEffect, useRef, useState } from "preact/hooks";

import type { Currency } from "../api/types";
import { formatMoney } from "../lib/money";
import { MAX_QTY, WEIGHT_DECIMALS } from "../sale/cart";
import { round } from "../sale/money";
import { Icon } from "../ui/Icon";

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
      <form class="modal-box flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); if (kilos > 0) onDone(kilos); }}>
        <div class="flex items-center gap-3">
          <span class="avatar-disc w-12 h-12" data-role="manager" aria-hidden="true"><Icon name="scale" size={24} /></span>
          <div class="min-w-0">
            <h3 class="text-xl truncate">{product.name}</h3>
            <div class="label-2 num">{formatMoney(product.list_price, currency)} / {unit}</div>
          </div>
        </div>
        <label class="input input-lg w-full !h-16 text-3xl num">
          <input ref={input} type="text" inputMode="decimal" autocomplete="off" placeholder="0.000" value={text} aria-label={`Peso en ${unit}`}
            onInput={(e) => setText(e.currentTarget.value)}
            onKeyDown={(e) => { if (e.key === "Escape") onCancel(); }} />
          <span class="label-2 text-xl">{unit}</span>
        </label>
        <div class="flex items-baseline justify-between">
          <span class="label-2">Importe</span>
          <span class="text-3xl font-semibold num">{formatMoney(round(product.list_price * kilos), currency)}</span>
        </div>
        <div class="modal-action">
          <button type="button" class="btn" onClick={onCancel}>Cancelar</button>
          <button class="btn btn-primary" disabled={kilos <= 0}>Agregar</button>
        </div>
      </form>
    </dialog>
  );
}
