import { useEffect, useRef, useState } from "preact/hooks";

import type { PosCategory } from "../api/types";
import { getMeta, type ProductRow, setMeta } from "../db/db";
import { browseProducts } from "../db/search";
import { formatMoney } from "../lib/money";
import { usePos } from "../state";
import { productImageUrl } from "../sync/images";
import { ProductEditDialog } from "./ProductEditDialog";

const META_CATEGORIES = "pos-categories";

/**
 * The register's products as tiles, as in the Odoo POS: category tabs, a
 * picture, the name and the price. Tapping a tile adds it; "i" shows the
 * description. `searched` replaces the browsing with search results.
 */
export function ProductGrid({ searched, onPick, onChanged }: {
  searched: ProductRow[] | null;
  onPick: (product: ProductRow) => void;
  /** A product was edited here: the caller refreshes what it shows (search results, cart). */
  onChanged?: (row: ProductRow) => void;
}) {
  const { db, client, setup, online, canEditCatalog, registerState, requestAuthorization, releaseAuthorization, authorizedBy } = usePos();
  // Without the right, someone allowed authorizes the edit with their PIN (padlock).
  const canAsk = online && !!registerState?.employee_login;
  async function startEdit(product: ProductRow, picture: string | null) {
    if (!canEditCatalog) {
      const ok = await requestAuthorization("Editar productos", "catalog", (e) => !!e.can_edit_catalog);
      if (!ok) return;
    }
    setEditing({ product, picture });
    setInfo(null);
  }
  function stopEdit() {
    setEditing(null);
    if (authorizedBy) releaseAuthorization();
  }
  const [editing, setEditing] = useState<{ product: ProductRow; picture: string | null } | null>(null);
  const [version, setVersion] = useState(0);
  const [categories, setCategories] = useState<PosCategory[]>([]);
  const [category, setCategory] = useState<number | null>(null);
  const [browsed, setBrowsed] = useState<{ items: ProductRow[]; more: boolean }>({ items: [], more: false });
  const [limit, setLimit] = useState(120);
  const [info, setInfo] = useState<ProductRow | null>(null);
  const register = setup.register;
  const allowed = register.limit_categories && register.available_pos_category_ids?.length
    ? register.available_pos_category_ids : null;

  // Categories: kept on the device, refreshed when online.
  useEffect(() => {
    void getMeta<PosCategory[]>(db, META_CATEGORIES).then((kept) => kept && setCategories(kept));
    if (!online) return;
    client.posCategories()
      .then(({ items }) => { setCategories(items); void setMeta(db, META_CATEGORIES, items); })
      .catch(() => undefined);
  }, [db, client, online]);

  useEffect(() => {
    let current = true;
    void browseProducts(db, category, allowed, limit).then((result) => { if (current) setBrowsed(result); });
    return () => { current = false; };
  }, [db, category, limit, allowed?.join(","), version]);

  // Top-level tabs, or the children of the open one (as the Odoo POS breadcrumb).
  const shown = categories.filter((c) => (allowed ? allowed.includes(c.id) : true));
  const open = shown.find((c) => c.id === category) ?? null;
  const children = shown.filter((c) => c.parent_id === (open ? open.id : null));
  const tabs = children.length ? children : shown.filter((c) => c.parent_id === (open?.parent_id ?? null));
  const items = searched ?? browsed.items;

  return (
    <div class="flex flex-col gap-2">
      {!searched && shown.length > 0 && (
        <div class="flex gap-1 overflow-x-auto pb-1" role="tablist" aria-label="Categorías">
          <button role="tab" class={`btn btn-sm ${category === null ? "btn-primary" : "btn-ghost"}`}
            onClick={() => { setCategory(null); setLimit(120); }}>Todos</button>
          {open?.parent_id != null && (
            <button class="btn btn-sm btn-ghost" onClick={() => setCategory(open.parent_id)}>‹ Atrás</button>
          )}
          {tabs.map((c) => (
            <button key={c.id} role="tab" class={`btn btn-sm whitespace-nowrap ${category === c.id ? "btn-primary" : "btn-ghost"}`}
              onClick={() => { setCategory(c.id); setLimit(120); }}>{c.name}</button>
          ))}
        </div>
      )}
      <div class="grid gap-2 grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))]">
        {items.map((product) => (
          <ProductTile key={product.id} product={product} onPick={onPick} onInfo={() => setInfo(product)} />
        ))}
      </div>
      {!searched && browsed.more && (
        <button class="btn btn-ghost btn-sm self-center" onClick={() => setLimit(limit + 120)}>Ver más productos</button>
      )}
      {!searched && !browsed.items.length && <p class="opacity-60">No hay productos en esta categoría.</p>}
      {info && <ProductInfo product={info} currency={setup.store.currency}
        onAdd={() => { onPick(info); setInfo(null); }} onClose={() => setInfo(null)}
        editLocked={!canEditCatalog}
        onEdit={online && (canEditCatalog || canAsk) ? (picture) => void startEdit(info, picture) : undefined} />}
      {editing && <ProductEditDialog product={editing.product} picture={editing.picture} categories={shown}
        onCancel={stopEdit}
        onSaved={(row) => { stopEdit(); setInfo(row); setVersion((v) => v + 1); onChanged?.(row); }} />}
    </div>
  );
}

function useImage(product: ProductRow, visible: boolean): string | null {
  const { db, client, online } = usePos();
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!visible || !product.image_version) return;
    let current = true;
    void productImageUrl(db, client, product, online).then((found) => { if (current) setUrl(found); });
    return () => { current = false; };
  }, [visible, product.id, product.image_version, online]);
  return url;
}

function ProductTile({ product, onPick, onInfo }: { product: ProductRow; onPick: (p: ProductRow) => void; onInfo: () => void }) {
  const { setup } = usePos();
  const tile = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  // Pictures load only for tiles on screen.
  useEffect(() => {
    const node = tile.current;
    if (!node) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry?.isIntersecting) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: "200px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const url = useImage(product, visible);
  return (
    <div ref={tile} class="card bg-base-100 shadow-sm relative overflow-hidden">
      <button class="flex flex-col text-left h-full" onClick={() => onPick(product)} aria-label={`Agregar ${product.name}`}>
        <div class="aspect-square bg-base-200 flex items-center justify-center">
          {url
            ? <img src={url} alt="" class="w-full h-full object-contain" loading="lazy" decoding="async" />
            : <span class="text-3xl font-bold opacity-30" aria-hidden="true">{initials(product.name)}</span>}
        </div>
        <div class="p-2 flex flex-col gap-1 flex-1">
          <span class="text-sm leading-tight line-clamp-2">{product.name}</span>
          <span class="font-semibold mt-auto">
            {formatMoney(product.list_price, setup.store.currency)}
            {product.to_weight && <span class="text-xs opacity-70">/{product.uom.name}</span>}
          </span>
        </div>
      </button>
      <button class="btn btn-circle btn-xs absolute top-1 right-1 bg-base-100/80" aria-label={`Información de ${product.name}`}
        onClick={onInfo}>i</button>
    </div>
  );
}

function ProductInfo({ product, currency, onAdd, onClose, onEdit, editLocked }: {
  product: ProductRow;
  currency: Parameters<typeof formatMoney>[1];
  onAdd: () => void;
  onClose: () => void;
  /** Present when the person at the register may edit products. */
  onEdit?: (picture: string | null) => void;
  /** Editing needs someone else's PIN. */
  editLocked?: boolean;
}) {
  const url = useImage(product, true);
  return (
    <dialog class="modal modal-open" aria-label={product.name}>
      <div class="modal-box flex flex-col gap-3">
        {url && <img src={url} alt="" class="max-h-64 object-contain self-center" />}
        <h3 class="text-lg font-bold">{product.name}</h3>
        <div class="text-xl font-semibold">
          {formatMoney(product.list_price, currency)}{product.to_weight && ` / ${product.uom.name}`}
        </div>
        {product.description
          ? <p class="whitespace-pre-line text-sm">{product.description}</p>
          : <p class="text-sm opacity-60">Sin descripción.</p>}
        <div class="text-xs opacity-60 font-mono">
          {product.barcode && <div>Código de barras: {product.barcode}</div>}
          {product.default_code && <div>Referencia: {product.default_code}</div>}
        </div>
        <div class="modal-action">
          <button class="btn" onClick={onClose}>Cerrar</button>
          {onEdit && <button class="btn" onClick={() => onEdit(url)}>{editLocked ? "🔒 Editar" : "Editar"}</button>}
          <button class="btn btn-primary" onClick={onAdd}>Agregar</button>
        </div>
      </div>
      <button class="modal-backdrop" aria-label="Cerrar" onClick={onClose} />
    </dialog>
  );
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word[0]?.toUpperCase() ?? "").join("");
}
