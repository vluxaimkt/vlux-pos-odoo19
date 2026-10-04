import { useState } from "preact/hooks";

import type { PosCategory, ProductChanges } from "../api/types";
import { productRow, type ProductRow } from "../db/db";
import { usePos } from "../state";
import { PhotoPicker } from "./PhotoPicker";
import { explain } from "./SetupScreen";

/**
 * Change a product from the register (price, name, description, barcode,
 * category, sold by weight, tax, picture). Only what changed is sent; the
 * server checks who may do it and answers the product as the catalog sends
 * it, which replaces the local copy at once.
 */
export function ProductEditDialog({ product, picture, categories, onSaved, onCancel }: {
  product: ProductRow;
  /** The picture shown now (a URL), if any. */
  picture: string | null;
  categories: PosCategory[];
  onSaved: (row: ProductRow) => void;
  onCancel: () => void;
}) {
  const { client, db, setup, taxes } = usePos();
  const [name, setName] = useState(product.name);
  const [price, setPrice] = useState(String(product.list_price));
  const [description, setDescription] = useState(product.description ?? "");
  const [barcode, setBarcode] = useState(product.barcode ?? "");
  const [category, setCategory] = useState(String(product.pos_category_ids[0] ?? ""));
  const [toWeight, setToWeight] = useState(!!product.to_weight);
  const [taxId, setTaxId] = useState(String(product.tax_ids[0] ?? ""));
  // undefined: untouched; null: removed; string: a new picture.
  const [image, setImage] = useState<string | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const saleTaxes = [...taxes.values()].filter((tax) => tax.active);

  async function save(event: Event) {
    event.preventDefault();
    setError(null);
    const listPrice = Number(price.replace(",", "."));
    if (!name.trim()) return setError("Escribe el nombre.");
    if (!Number.isFinite(listPrice) || listPrice < 0) return setError("Escribe un precio válido.");
    const changes: ProductChanges = {};
    if (name.trim() !== product.name) changes.name = name.trim();
    if (Math.abs(listPrice - product.list_price) > 0.0001) changes.list_price = Math.round(listPrice * 100) / 100;
    if (description.trim() !== (product.description ?? "")) changes.description = description.trim();
    if (barcode.trim() !== (product.barcode ?? "")) changes.barcode = barcode.trim();
    if (category !== String(product.pos_category_ids[0] ?? "")) changes.pos_categ_id = category ? Number(category) : null;
    if (toWeight !== !!product.to_weight) changes.to_weight = toWeight;
    if (taxId !== String(product.tax_ids[0] ?? "")) changes.taxes_ids = taxId ? [Number(taxId)] : [];
    if (image !== undefined) changes.image = image;
    if (!Object.keys(changes).length) return onCancel();
    setBusy(true);
    try {
      const fresh = productRow(await client.editProduct(setup.register.id, product.id, changes));
      await db.products.put(fresh);
      onSaved(fresh);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog class="modal modal-open" aria-label={`Editar ${product.name}`}>
      <form class="modal-box flex flex-col gap-2" onSubmit={(e) => void save(e)}>
        <h3 class="text-lg font-bold">Editar producto</h3>
        <label class="text-sm">Nombre
          <input class="input w-full" type="text" maxLength={120} value={name} onInput={(e) => setName(e.currentTarget.value)} />
        </label>
        <label class="text-sm">Precio de venta{toWeight ? ` (por ${product.uom.name})` : ""}
          <input class="input w-full" type="text" inputMode="decimal" value={price} onInput={(e) => setPrice(e.currentTarget.value)} />
        </label>
        <label class="text-sm">Descripción
          <textarea class="textarea w-full" rows={3} maxLength={2000} value={description}
            onInput={(e) => setDescription(e.currentTarget.value)} />
        </label>
        <label class="text-sm">Código de barras
          <input class="input w-full font-mono" type="text" maxLength={128} value={barcode} onInput={(e) => setBarcode(e.currentTarget.value)} />
        </label>
        <div class="grid grid-cols-2 gap-2">
          <label class="text-sm">Categoría
            <select class="select w-full" value={category} onChange={(e) => setCategory(e.currentTarget.value)}>
              <option value="">Sin categoría</option>
              {categories.map((c) => <option key={c.id} value={String(c.id)}>{c.name}</option>)}
            </select>
          </label>
          <label class="text-sm">Impuesto
            <select class="select w-full" value={taxId} onChange={(e) => setTaxId(e.currentTarget.value)}>
              <option value="">Sin impuesto</option>
              {saleTaxes.map((tax) => <option key={tax.id} value={String(tax.id)}>{tax.name}</option>)}
            </select>
          </label>
        </div>
        <label class="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" class="checkbox checkbox-sm" checked={toWeight} onChange={(e) => setToWeight(e.currentTarget.checked)} />
          Se vende por peso
        </label>
        <PhotoPicker value={image === undefined ? picture : image} onChange={setImage}
          barcode={barcode || undefined} label={name.trim()} canRemove />
        {error && <div role="alert" class="alert alert-error">{error}</div>}
        <div class="modal-action">
          <button type="button" class="btn" onClick={onCancel}>Cancelar</button>
          <button class="btn btn-primary" disabled={busy}>{busy ? <span class="loading loading-spinner" /> : "Guardar cambios"}</button>
        </div>
      </form>
    </dialog>
  );
}
