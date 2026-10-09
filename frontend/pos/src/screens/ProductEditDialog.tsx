import { useEffect, useState } from "preact/hooks";

import type { PosCategory, ProductChanges, ProductDetails } from "../api/types";
import { productRow, type ProductRow } from "../db/db";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { formatMoney } from "../lib/money";
import { Banner, Field, MarginHint } from "../ui/Page";
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
  // The purchase price and stock come apart: only editors may see them.
  const [details, setDetails] = useState<ProductDetails | null>(null);
  const [cost, setCost] = useState("");
  useEffect(() => {
    let current = true;
    client.productDetails(setup.register.id, product.id)
      .then((found) => {
        if (!current) return;
        setDetails(found);
        setCost(found.standard_price ? String(found.standard_price) : "");
      })
      .catch(() => undefined);
    return () => { current = false; };
  }, [product.id]);
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
    if (details) {
      const standardPrice = cost.trim() ? Number(cost.replace(",", ".")) : 0;
      if (!Number.isFinite(standardPrice) || standardPrice < 0) return setError("El precio de compra no es válido.");
      if (Math.abs(standardPrice - details.standard_price) > 0.0001) changes.standard_price = Math.round(standardPrice * 100) / 100;
    }
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
      <form class="modal-box flex flex-col gap-4 max-h-[90vh] overflow-y-auto" onSubmit={(e) => void save(e)}>
        <div class="flex items-center gap-3">
          <span class="avatar-disc w-12 h-12 shrink-0" data-role="manager" aria-hidden="true"><Icon name="info" size={24} /></span>
          <div class="min-w-0">
            <h3 class="text-xl">Editar producto</h3>
            <div class="text-sm label-2 truncate">{product.name}</div>
          </div>
        </div>
        <Field label="Nombre">
          <input class="input w-full" type="text" maxLength={120} value={name} onInput={(e) => setName(e.currentTarget.value)} />
        </Field>
        <Field label={`Precio de venta${toWeight ? ` (por ${product.uom.name})` : ""}`}>
          <label class="input w-full num">
            <span class="label-2">$</span>
            <input type="text" inputMode="decimal" value={price} aria-label="Precio de venta" onInput={(e) => setPrice(e.currentTarget.value)} />
          </label>
        </Field>
        {details && (
          <div class="grid grid-cols-2 gap-4">
            <Field label="Precio de compra">
              <label class="input w-full num">
                <span class="label-2">$</span>
                <input type="text" inputMode="decimal" value={cost} placeholder="0.00" aria-label="Precio de compra"
                  onInput={(e) => setCost(e.currentTarget.value)} />
              </label>
            </Field>
            {details.tracks_stock && (
              <Field label="Existencia">
                <span class="input w-full num items-center label-2">
                  {details.qty_available} {product.uom.name === "Unidades" ? "pzas" : product.uom.name}
                </span>
              </Field>
            )}
          </div>
        )}
        {details && <MarginHint price={Number(price.replace(",", "."))} cost={Number(cost.replace(",", "."))}
          money={(n) => formatMoney(n, setup.store.currency)} />}
        <Field label="Descripción">
          <textarea class="textarea w-full" rows={3} maxLength={2000} value={description}
            onInput={(e) => setDescription(e.currentTarget.value)} />
        </Field>
        <Field label="Código de barras">
          <input class="input w-full font-mono" type="text" maxLength={128} value={barcode} onInput={(e) => setBarcode(e.currentTarget.value)} />
        </Field>
        <div class="grid grid-cols-2 gap-4">
          <Field label="Categoría">
            <select class="select w-full" value={category} onChange={(e) => setCategory(e.currentTarget.value)}>
              <option value="">Sin categoría</option>
              {categories.map((c) => <option key={c.id} value={String(c.id)}>{c.name}</option>)}
            </select>
          </Field>
          <Field label="Impuesto">
            <select class="select w-full" value={taxId} onChange={(e) => setTaxId(e.currentTarget.value)}>
              <option value="">Sin impuesto</option>
              {saleTaxes.map((tax) => <option key={tax.id} value={String(tax.id)}>{tax.name}</option>)}
            </select>
          </Field>
        </div>
        <div class="grouped">
          <label class="row cursor-pointer">
            <span class="flex-1 font-medium">Se vende por peso</span>
            <input type="checkbox" class="toggle" checked={toWeight} onChange={(e) => setToWeight(e.currentTarget.checked)} />
          </label>
        </div>
        <PhotoPicker value={image === undefined ? picture : image} onChange={setImage}
          barcode={barcode || undefined} label={name.trim()} canRemove />
        {error && <Banner tone="error">{error}</Banner>}
        <div class="modal-action mt-0">
          <button type="button" class="btn" onClick={onCancel}>Cancelar</button>
          <button class="btn btn-primary" disabled={busy}>{busy ? <span class="loading loading-spinner" /> : "Guardar cambios"}</button>
        </div>
      </form>
    </dialog>
  );
}
