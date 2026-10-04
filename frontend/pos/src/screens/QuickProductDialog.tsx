import { useState } from "preact/hooks";

import type { Product } from "../api/types";
import { usePos } from "../state";
import { explain } from "./SetupScreen";

/**
 * Add a product the store does not have yet, from its scanned barcode (as the
 * Odoo POS quick create). The server checks that a manager is at the
 * register and that the code is not taken.
 */
export function QuickProductDialog({ barcode, onCreated, onCancel }: {
  barcode: string;
  onCreated: (product: Product) => void | Promise<void>;
  onCancel: () => void;
}) {
  const { client, setup, taxes } = usePos();
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const [taxId, setTaxId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const saleTaxes = [...taxes.values()].filter((tax) => tax.active);
  // Whether the price typed includes taxes follows the store's tax setup, not a fixed assumption.
  const applied = taxId ? [taxes.get(Number(taxId))] : (setup.store.default_sale_tax_ids ?? []).map((id) => taxes.get(id));
  const known = applied.filter((tax) => !!tax);
  const included = known.length
    ? known.every((tax) => tax!.price_include)
    : setup.store.price_include_default === "tax_included" || setup.store.price_include_default === true;
  const priceHint = !known.length && !setup.store.price_include_default
    ? "Precio de venta"
    : included ? "Precio de venta (impuestos incluidos)" : "Precio de venta (antes de impuestos)";

  async function submit(event: Event) {
    event.preventDefault();
    setError(null);
    const listPrice = Number(price.replace(",", "."));
    if (!name.trim()) return setError("Escribe el nombre del producto.");
    if (!Number.isFinite(listPrice) || listPrice <= 0) return setError("Escribe el precio de venta.");
    setBusy(true);
    try {
      const product = await client.quickProduct(setup.register.id, {
        name: name.trim(), barcode, list_price: Math.round(listPrice * 100) / 100,
        ...(taxId ? { taxes_ids: [Number(taxId)] } : {}),
      });
      await onCreated(product);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog class="modal modal-open" aria-label="Alta rápida de producto">
      <form class="modal-box flex flex-col gap-3" onSubmit={(e) => void submit(e)}>
        <h3 class="text-lg font-bold">Producto nuevo</h3>
        <div class="font-mono opacity-70">{barcode}</div>
        <input class="input w-full" type="text" maxLength={120} placeholder="Nombre (como en el empaque)" autofocus
          value={name} onInput={(e) => setName(e.currentTarget.value)} />
        <label class="input w-full">
          <span class="opacity-70">$</span>
          <input type="text" inputMode="decimal" autocomplete="off" placeholder={priceHint} aria-label={priceHint} value={price}
            onInput={(e) => setPrice(e.currentTarget.value)} />
        </label>
        <select class="select w-full" value={taxId} onChange={(e) => setTaxId(e.currentTarget.value)}>
          <option value="">Impuesto predeterminado de la tienda</option>
          {saleTaxes.map((tax) => <option key={tax.id} value={String(tax.id)}>{tax.name}</option>)}
        </select>
        {error && <div role="alert" class="alert alert-error">{error}</div>}
        <div class="modal-action">
          <button type="button" class="btn" onClick={onCancel}>Cancelar</button>
          <button class="btn btn-primary" disabled={busy}>{busy ? <span class="loading loading-spinner" /> : "Dar de alta y vender"}</button>
        </div>
      </form>
    </dialog>
  );
}
