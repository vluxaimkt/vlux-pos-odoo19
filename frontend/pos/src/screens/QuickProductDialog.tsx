import { useState } from "preact/hooks";

import type { Product } from "../api/types";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { Banner, Field } from "../ui/Page";
import { PhotoPicker } from "./PhotoPicker";
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
  const [image, setImage] = useState<string | null>(null);
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
        ...(image ? { image } : {}),
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
      <form class="modal-box flex flex-col gap-4" onSubmit={(e) => void submit(e)}>
        <div class="flex items-center gap-3">
          <span class="avatar-disc w-12 h-12 shrink-0" data-role="manager" aria-hidden="true"><Icon name="plus" size={24} /></span>
          <div class="min-w-0">
            <h3 class="text-xl">Producto nuevo</h3>
            <div class="font-mono text-sm label-2 truncate">{barcode}</div>
          </div>
        </div>
        <Field label="Nombre" hint="Como viene en el empaque.">
          <input class="input w-full" type="text" maxLength={120} autofocus
            value={name} onInput={(e) => setName(e.currentTarget.value)} />
        </Field>
        <Field label={priceHint}>
          <label class="input w-full num">
            <span class="label-2">$</span>
            <input type="text" inputMode="decimal" autocomplete="off" placeholder="0.00" aria-label={priceHint} value={price}
              onInput={(e) => setPrice(e.currentTarget.value)} />
          </label>
        </Field>
        <Field label="Impuesto">
          <select class="select w-full" value={taxId} onChange={(e) => setTaxId(e.currentTarget.value)}>
            <option value="">Impuesto predeterminado de la tienda</option>
            {saleTaxes.map((tax) => <option key={tax.id} value={String(tax.id)}>{tax.name}</option>)}
          </select>
        </Field>
        <PhotoPicker value={image} onChange={setImage} barcode={barcode} label={name.trim()} canRemove />
        {error && <Banner tone="error">{error}</Banner>}
        <p class="text-xs label-2">El formulario se queda abierto hasta que lo des de alta o lo canceles.</p>
        <div class="modal-action mt-0">
          <button type="button" class="btn" onClick={onCancel}>Cancelar</button>
          <button class="btn btn-primary" disabled={busy}>{busy ? <span class="loading loading-spinner" /> : "Dar de alta y vender"}</button>
        </div>
      </form>
    </dialog>
  );
}
