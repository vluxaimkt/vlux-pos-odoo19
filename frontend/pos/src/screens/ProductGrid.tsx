import { useEffect, useRef, useState } from "preact/hooks";

import type { PosCategory } from "../api/types";
import { getMeta, type ProductRow, setMeta } from "../db/db";
import { browseProducts } from "../db/search";
import { formatMoney } from "../lib/money";
import { usePos } from "../state";
import { productImageUrl } from "../sync/images";
import { ProductEditDialog } from "./ProductEditDialog";
import { Icon } from "../ui/Icon";

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
    <div class="flex flex-col gap-4">
      {!searched && shown.length > 0 && (
        <div class="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1" role="tablist" aria-label="Categorías">
          <button role="tab" class="chip" aria-selected={category === null}
            onClick={() => { setCategory(null); setLimit(120); }}>Todos</button>
          {open?.parent_id != null && (
            <button class="chip" onClick={() => setCategory(open.parent_id)}><Icon name="back" size={16} /> Atrás</button>
          )}
          {tabs.map((c) => (
            <button key={c.id} role="tab" class="chip" aria-selected={category === c.id}
              onClick={() => { setCategory(c.id); setLimit(120); }}>{c.name}</button>
          ))}
        </div>
      )}
      <div class="grid gap-4 grid-cols-[repeat(auto-fill,minmax(8rem,1fr))]">
        {items.map((product) => (
          <ProductTile key={product.id} product={product} onPick={onPick} onInfo={() => setInfo(product)} />
        ))}
      </div>
      {!searched && browsed.more && (
        <button class="btn btn-ghost text-primary self-center" onClick={() => setLimit(limit + 120)}>Ver más productos</button>
      )}
      {!searched && !browsed.items.length && <p class="label-2 py-16 text-center">No hay productos en esta categoría.</p>}
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
    <div ref={tile} class="tile">
      <button class="tile-main flex flex-col text-left h-full" onClick={() => onPick(product)} aria-label={`Agregar ${product.name}`}>
        <div class="tile-art" style={url ? undefined : { background: tint(product.name) }}>
          {url
            ? <img src={url} alt="" class="w-full h-full object-contain p-2" loading="lazy" decoding="async" />
            : <span class="text-3xl font-semibold text-white/90 tracking-tight" aria-hidden="true">{initials(product.name)}</span>}
        </div>
        <div class="p-3 flex flex-col gap-1 flex-1">
          <span class="text-sm font-medium leading-tight line-clamp-2">{product.name}</span>
          <span class="font-semibold mt-auto num">
            {formatMoney(product.list_price, setup.store.currency)}
            {product.to_weight && <span class="text-xs font-normal label-2"> /{product.uom.name}</span>}
          </span>
        </div>
      </button>
      <button class="info-dot" aria-label={`Información de ${product.name}`} onClick={onInfo}>
        <Icon name="info" size={18} />
      </button>
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
      <div class="modal-box flex flex-col gap-4">
        {url
          ? <img src={url} alt="" class="max-h-64 object-contain self-center rounded-[12px]" />
          : <div class="h-32 rounded-[12px] grid place-items-center text-4xl font-semibold text-white/90" style={{ background: tint(product.name) }} aria-hidden="true">{initials(product.name)}</div>}
        <h3 class="text-2xl">{product.name}</h3>
        <div class="text-xl font-semibold num">
          {formatMoney(product.list_price, currency)}{product.to_weight && ` / ${product.uom.name}`}
        </div>
        {product.description
          ? <p class="whitespace-pre-line text-sm">{product.description}</p>
          : <p class="text-sm label-2">Sin descripción.</p>}
        <div class="text-xs label-2 font-mono">
          {product.barcode && <div>Código de barras: {product.barcode}</div>}
          {product.default_code && <div>Referencia: {product.default_code}</div>}
        </div>
        <div class="modal-action">
          <button class="btn" onClick={onClose}>Cerrar</button>
          {onEdit && <button class="btn" onClick={() => onEdit(url)}>{editLocked && <Icon name="lock" size={18} />}Editar</button>}
          <button class="btn btn-primary" onClick={onAdd}>Agregar</button>
        </div>
      </div>
      <button class="modal-backdrop" aria-label="Cerrar" onClick={onClose} />
    </dialog>
  );
}

/** A soft gradient per product (stable by name), in Apple's system hues, for tiles without a picture. */
const HUES = [
  ["#5ac8fa", "#007aff"], ["#34c759", "#248a3d"], ["#ff9f0a", "#ff6b00"], ["#ff6482", "#ff2d55"],
  ["#bf5af2", "#8944ab"], ["#64d2ff", "#30b0c7"], ["#ffd60a", "#ff9f0a"], ["#7d7aff", "#5856d6"],
];
function tint(name: string): string {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const [from, to] = HUES[hash % HUES.length]!;
  return `linear-gradient(160deg, ${from}, ${to})`;
}

function initials(name: string): string {
  return name.split(/\s+/).filter((word) => /^[\p{L}\p{N}]/u.test(word)).slice(0, 2).map((word) => word[0]?.toUpperCase() ?? "").join("");
}
