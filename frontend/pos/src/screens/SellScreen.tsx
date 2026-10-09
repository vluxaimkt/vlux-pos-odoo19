import { useEffect, useRef, useState } from "preact/hooks";

import { isRetryable } from "../api/client";
import type { OrderRequest } from "../api/types";
import { getMeta, productRow, type ProductRow, setMeta } from "../db/db";
import { productByBarcode, searchProducts } from "../db/search";
import { parseBarcode } from "../lib/barcode";
import { formatMoney } from "../lib/money";
import {
  addProduct, type AddOptions, type Cart, emptyCart, isWeighed, itemCount, qtyLabel, refreshProduct, removeLine, setCustomer, setQty,
  setWholesalePrice,
  unitPrice,
} from "../sale/cart";
import { fromQuote, localPricing, type Pricing } from "../sale/pricing";
import { META_CART, usePos } from "../state";
import { CreditLine, CustomersScreen } from "./CustomersScreen";
import { type CreditTicket, PayScreen } from "./PayScreen";
import { ReceiptScreen } from "./ReceiptScreen";
import { explain } from "./SetupScreen";
import { ProductGrid } from "./ProductGrid";
import { QuickProductDialog } from "./QuickProductDialog";
import { WeighDialog } from "./WeighDialog";
import { WholesaleDialog } from "./WholesaleDialog";
import { type ScanOutcome } from "../input/sources";
import { PhoneScannerButton, usePhoneScanner } from "./PhoneScanner";
import { Icon } from "../ui/Icon";

const newId = () => crypto.randomUUID();

type Stage =
  | { name: "cart" }
  | { name: "customer" }
  | { name: "pay"; pricing: Pricing }
  | { name: "receipt"; order: OrderRequest; pricing: Pricing; credit: CreditTicket | null };

/** Ring up a sale: scan or search, adjust the cart, charge, print the ticket. */
export function SellScreen() {
  const { db, client, setup, online, taxes, credit, canEditCatalog, registerState, requestAuthorization, releaseAuthorization, authorizedBy } = usePos();
  const [cart, setCart] = useState<Cart | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProductRow[]>([]);
  // Bumped when a product is edited, so search results show the change.
  const [searchVersion, setSearchVersion] = useState(0);
  const [stage, setStage] = useState<Stage>({ name: "cart" });
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** A product sold by weight waiting for its kilos (new line, or re-weighing a line). */
  /** The line whose wholesale price is being typed. */
  const [wholesaleLine, setWholesaleLine] = useState<string | null>(null);
  const [weighing, setWeighing] = useState<{ product: ProductRow | Cart["lines"][number]["product"]; lineUuid?: string; qty?: number } | null>(null);
  /** A scanned code the store does not know: a manager may add it on the spot. */
  const [unknown, setUnknown] = useState<string | null>(null);
  // The code being added: the dialog keeps it, so the notice clearing itself never closes the form.
  const [creating, setCreating] = useState<string | null>(null);
  const canCreate = online && canEditCatalog;
  // Without the right, someone allowed authorizes it with their PIN (padlock).
  const canAskCreate = online && !canEditCatalog && !!registerState?.employee_login;
  async function startCreate(code: string) {
    setNotice(null);
    if (!canEditCatalog) {
      const ok = await requestAuthorization("Dar de alta productos", "catalog", (e) => !!e.can_edit_catalog);
      if (!ok) return;
    }
    setCreating(code);
  }
  function stopCreate() {
    setCreating(null);
    if (authorizedBy) releaseAuthorization();
  }
  const search = useRef<HTMLInputElement>(null);
  const cartPanel = useRef<HTMLElement>(null);
  const rounding = setup.store.tax_rounding;

  // The latest cart, for scans that arrive one after another (phone, scanner).
  const cartRef = useRef<Cart | null>(null);

  // The cart survives a reload of the app (power cut, accidental refresh).
  useEffect(() => {
    void getMeta<Cart>(db, META_CART).then((saved) => {
      cartRef.current = saved ?? emptyCart(newId);
      setCart(cartRef.current);
    });
  }, [db]);

  function update(next: Cart) {
    cartRef.current = next;
    setCart(next);
    void setMeta(db, META_CART, next);
  }

  // A notice clears itself; typing or scanning again also clears it.
  useEffect(() => {
    if (!notice) return;
    // Longer when it offers to add the unknown product.
    const id = setTimeout(() => { setNotice(null); setUnknown(null); }, unknown && (canCreate || canAskCreate) ? 15000 : 4000);
    return () => clearTimeout(id);
  }, [notice]);

  useEffect(() => {
    let current = true;
    void (async () => {
      const found = query.trim() ? await searchProducts(db, query, 30) : [];
      if (current) setResults(found);
    })();
    return () => {
      current = false;
    };
  }, [db, query, searchVersion]);

  /** Add to the cart; false when it waits for the weight. */
  function add(product: ProductRow, qty?: number, options: AddOptions = {}): boolean {
    const current = cartRef.current;
    if (!current) return false;
    setQuery("");
    setNotice(null);
    // Sold by weight with no weight yet: ask for the kilos.
    if (product.to_weight && qty === undefined && options.priceUnit === undefined) {
      setWeighing({ product });
      return false;
    }
    update(addProduct(current, product, newId, qty ?? 1, options));
    search.current?.focus();
    return true;
  }

  function weighed(kilos: number) {
    const current = cartRef.current;
    if (!current || !weighing) return;
    update(weighing.lineUuid ? setQty(current, weighing.lineUuid, kilos) : addProduct(current, weighing.product, newId, kilos));
    setWeighing(null);
    search.current?.focus();
  }

  /**
   * One read from any barcode source (keyboard-wedge scanner, linked phone…):
   * the same rules whatever the device, and an outcome the device can show.
   */
  async function handleScan(code: string): Promise<ScanOutcome> {
    const added = (product: ProductRow, ok: boolean): ScanOutcome => ok
      ? { status: "delivered", code: "ADDED_TO_CART", message: "Agregado", product: { id: product.id, name: product.name, price: product.list_price } }
      : { status: "delivered", code: "WEIGHT_REQUIRED", message: "Captura el peso en la caja", product: { id: product.id, name: product.name, price: product.list_price } };
    // A scale label carries the weight or the price; the product has the code with that part zeroed.
    const parsed = parseBarcode(code, setup.store.barcode_nomenclature);
    if ((parsed.type === "weight" || parsed.type === "price") && parsed.value > 0) {
      const labelled = (await productByBarcode(db, parsed.baseCode)) ?? (await productByBarcode(db, code));
      if (labelled) {
        return added(labelled, parsed.type === "weight" ? add(labelled, parsed.value) : add(labelled, 1, { priceUnit: parsed.value }));
      }
    }
    const product = await productByBarcode(db, code);
    if (product) return added(product, add(product));
    setNotice(`No se encontró "${code}" en esta caja.`);
    // A scale label of an unknown product is not a product code: set that product up in Odoo.
    setUnknown(parsed.type === "weight" || parsed.type === "price" ? null : code);
    setQuery("");
    return { status: "not_found", code: "PRODUCT_NOT_FOUND", message: "No está en esta caja" };
  }
  const scanRef = useRef(handleScan);
  scanRef.current = handleScan;
  usePhoneScanner((code) => scanRef.current(code));

  /** A keyboard-wedge scanner types the code and presses Enter; typing a name and Enter picks the only match. */
  async function onEnter(event: KeyboardEvent) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const code = query.trim();
    if (!code) return;
    if (!(await productByBarcode(db, code)) && results.length === 1 && results[0] && !/^\d{8,}$/.test(code)) {
      add(results[0]);
      return;
    }
    await handleScan(code);
  }

  async function charge() {
    if (!cart?.lines.length) return;
    setBusy(true);
    setNotice(null);
    try {
      let pricing: Pricing | null = null;
      if (online) {
        try {
          const quote = await client.quote({
            register_id: setup.register.id,
            lines: cart.lines.map((line) => ({
              uuid: line.uuid, product_id: line.product.id, qty: line.qty,
              ...(line.priceFromBarcode ? { price_unit: unitPrice(line), price_from_barcode: true } : {}),
              ...(line.wholesale ? { price_unit: unitPrice(line), wholesale: true } : {}),
            })),
            ...(cart.customer ? { partner_id: cart.customer.id } : {}),
          });
          pricing = fromQuote(cart, quote);
        } catch (error) {
          if (!isRetryable(error)) throw error;
        }
      }
      pricing ??= localPricing(cart, taxes, setup.register.use_pricelist, rounding);
      if (!pricing.exact) {
        setNotice("Sin internet no se puede calcular el total exacto de esta venta (impuestos o precios especiales). Conéctate para cobrarla.");
        return;
      }
      setStage({ name: "pay", pricing });
    } catch (error) {
      setNotice(explain(error));
    } finally {
      setBusy(false);
    }
  }

  function finished(order: OrderRequest, pricing: Pricing, creditTicket: CreditTicket | null) {
    update(emptyCart(newId));
    setStage({ name: "receipt", order, pricing, credit: creditTicket });
  }

  if (!cart) return null;
  if (stage.name === "customer") {
    return (
      <CustomersScreen
        onClose={() => setStage({ name: "cart" })}
        onPick={(customer) => {
          update(setCustomer(cart, customer));
          setStage({ name: "cart" });
        }}
      />
    );
  }
  if (stage.name === "pay") {
    return <PayScreen cart={cart} pricing={stage.pricing} onBack={() => setStage({ name: "cart" })} onPaid={finished} />;
  }
  if (stage.name === "receipt") {
    return <ReceiptScreen order={stage.order} pricing={stage.pricing} credit={stage.credit} onNext={() => setStage({ name: "cart" })} />;
  }

  const estimate = localPricing(cart, taxes, setup.register.use_pricelist, rounding);
  const money = (value: number) => formatMoney(value, setup.store.currency);
  const count = itemCount(cart);
  return (
    <section class="p-4 pb-32 lg:p-6 grid gap-6 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-[minmax(0,1fr)_24rem] items-start">
      <div class="flex flex-col gap-4 min-w-0">
        <div class="flex items-center gap-2">
          <label class="search-field flex-1">
            <Icon name="search" size={22} />
            <input
              ref={search}
              type="search"
              placeholder="Escanea o busca un producto"
              aria-label="Escanea o busca un producto"
              autofocus
              autocomplete="off"
              value={query}
              onInput={(event) => {
                setQuery(event.currentTarget.value);
                setNotice(null);
              }}
              onKeyDown={(event) => void onEnter(event)}
            />
            {query && (
              <button type="button" class="info-dot !static !w-6 !h-6 !text-[var(--label-secondary)]" aria-label="Borrar búsqueda"
                onClick={() => { setQuery(""); search.current?.focus(); }}>
                <Icon name="close" size={14} />
              </button>
            )}
          </label>
          <PhoneScannerButton />
        </div>
        {notice && (
          <div role="alert" class="surface rise flex items-center gap-3 px-4 py-3">
            <Icon name="warning" class="text-warning" />
            <span class="flex-1">{notice}</span>
            {unknown && (canCreate || canAskCreate) && (
              <button class="btn btn-sm btn-primary" onClick={() => void startCreate(unknown)}>{!canCreate && <Icon name="lock" size={16} />}Dar de alta</button>
            )}
          </div>
        )}
        <ProductGrid searched={query.trim() ? results : null} onPick={(product) => add(product)}
          onChanged={(row) => {
            setSearchVersion((v) => v + 1);
            if (cartRef.current) update(refreshProduct(cartRef.current, row));
          }} />
        {query && !results.length && (
          <div class="py-16 flex flex-col items-center gap-2 text-center label-2">
            <Icon name="search" size={40} />
            <p class="font-semibold text-base-content">Sin resultados</p>
            <p class="text-sm">Nada en esta caja coincide con "{query}".</p>
          </div>
        )}
      </div>

      {/* Phones: the cart sits below the products, so its total and "Cobrar" float at the bottom. */}
      {cart.lines.length > 0 && (
        <div class="lg:hidden glass fixed bottom-4 inset-x-4 z-20 rounded-[20px] p-2 pl-4 flex items-center gap-3 shadow-xl rise print:hidden">
          <button class="flex-1 min-w-0 text-left" onClick={() => cartPanel.current?.scrollIntoView({ behavior: "smooth" })}>
            <div class="text-xs label-2 num">{count} {count === 1 ? "artículo" : "artículos"} · ver venta</div>
            <div class="text-xl font-semibold num">{money(estimate.total)}</div>
          </button>
          <button class="btn btn-primary btn-xl" disabled={busy} onClick={() => void charge()}>
            {busy ? <span class="loading loading-spinner" /> : "Cobrar"}
          </button>
        </div>
      )}

      <aside ref={cartPanel} class="surface min-w-0 flex flex-col lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)]" aria-label="Venta">
        <header class="px-4 pt-4 pb-2 flex items-baseline gap-2">
          <h2 class="text-2xl flex-1">Venta</h2>
          {count > 0 && <span class="label-2 text-sm num">{count} {count === 1 ? "artículo" : "artículos"}</span>}
        </header>
        <div class="px-4 pb-2">
          <div class="grouped !bg-transparent">
            <div class="row !px-3 !min-h-14 bg-[var(--fill)]">
              <span class="avatar-disc w-8 h-8 text-sm" data-role={cart.customer ? "manager" : undefined}>
                {cart.customer ? (cart.customer.name.trim()[0] ?? "?").toUpperCase() : <Icon name="person" size={18} />}
              </span>
              <button class="flex-1 min-w-0 text-left" onClick={() => setStage({ name: "customer" })}>
                {cart.customer ? (
                  <>
                    <div class="font-semibold truncate">{cart.customer.name}</div>
                    <CreditLine row={credit.get(cart.customer.id)} money={money} />
                  </>
                ) : (
                  <span class="label-2">Agregar cliente</span>
                )}
              </button>
              {cart.customer
                ? <button class="btn btn-ghost btn-sm btn-square label-2" aria-label="Quitar cliente" onClick={() => update(setCustomer(cart, null))}><Icon name="close" size={16} /></button>
                : <Icon name="chevron" size={18} class="label-2" />}
            </div>
          </div>
        </div>
        {!cart.lines.length ? (
          <div class="flex-1 py-16 px-4 flex flex-col items-center justify-center gap-2 text-center label-2">
            <Icon name="cart" size={40} />
            <p class="font-semibold text-base-content">Carrito vacío</p>
            <p class="text-sm">Escanea o toca un producto para empezar.</p>
          </div>
        ) : (
          <ul class="flex-1 overflow-y-auto px-4 min-h-0 max-h-[50vh] lg:max-h-none">
            {cart.lines.map((line) => (
              <li key={line.uuid} class="py-3 border-b border-[var(--hairline)] last:border-0 rise flex flex-col gap-2">
                <div class="flex items-start gap-3">
                  <div class="flex-1 min-w-0 font-medium leading-snug line-clamp-2">{line.product.name}</div>
                  {!line.priceFromBarcode && (
                    <button type="button" class={`chip !h-7 !px-3 text-xs shrink-0 ${line.wholesale ? "!bg-[#ff9500] !text-white" : ""}`}
                      aria-pressed={!!line.wholesale} title="Precio de mayoreo para este cliente"
                      onClick={() => setWholesaleLine(line.uuid)}>Mayoreo</button>
                  )}
                  <span class="font-semibold num">{money(unitPrice(line) * line.qty)}</span>
                </div>
                <div class="flex items-center gap-2">
                  <div class="flex-1 min-w-0 text-xs label-2 num truncate">
                    {isWeighed(line)
                      ? `${qtyLabel(line.qty, line.product.uom?.name ?? "kg")} × ${money(unitPrice(line))}`
                      : `${money(unitPrice(line))} c/u`}
                    {line.priceFromBarcode && " · precio de etiqueta"}
                    {line.wholesale && " · precio de mayoreo"}
                  </div>
                  {isWeighed(line) ? (
                    <button class="btn btn-sm" onClick={() => setWeighing({ product: line.product, lineUuid: line.uuid, qty: line.qty })}>
                      <Icon name="scale" size={16} /> Pesar
                    </button>
                  ) : (
                    <div class="stepper">
                      <button aria-label="Menos" onClick={() => update(setQty(cart, line.uuid, line.qty - 1))}><Icon name="minus" size={16} /></button>
                      <output aria-label="Cantidad">{line.qty}</output>
                      <button aria-label="Más" onClick={() => update(setQty(cart, line.uuid, line.qty + 1))}><Icon name="plus" size={16} /></button>
                    </div>
                  )}
                  <button class="btn btn-ghost btn-sm btn-square label-2" aria-label="Quitar" onClick={() => update(removeLine(cart, line.uuid))}>
                    <Icon name="trash" size={16} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <footer class="p-4 border-t border-[var(--hairline)] flex flex-col gap-3">
          <div class="flex items-baseline justify-between">
            <span class="label-2 font-medium">Total</span>
            <span class="text-4xl font-semibold num">{money(estimate.total)}</span>
          </div>
          {!estimate.exact && <p class="text-xs label-2 -mt-2 text-right">El total final lo confirma el servidor al cobrar.</p>}
          <button class="btn btn-primary btn-xl" disabled={!cart.lines.length || busy} onClick={() => void charge()}>
            {busy ? <span class="loading loading-spinner" /> : "Cobrar"}
          </button>
          {cart.lines.length > 0 && (
            <button class="btn btn-ghost btn-sm text-danger" onClick={() => { if (confirm("¿Cancelar esta venta?")) update(emptyCart(newId)); }}>
              <Icon name="trash" size={16} /> Cancelar venta
            </button>
          )}
        </footer>
      </aside>
      {creating && (
        <QuickProductDialog barcode={creating}
          onCancel={() => { stopCreate(); search.current?.focus(); }}
          onCreated={async (product) => {
            // Into the local catalog now; the next sync brings the same row.
            const row = productRow(product);
            await db.products.put(row);
            stopCreate();
            setUnknown(null);
            add(row);
          }} />
      )}
      {(() => {
        const line = wholesaleLine ? cart.lines.find((candidate) => candidate.uuid === wholesaleLine) : undefined;
        if (!line) return null;
        const close = () => { setWholesaleLine(null); search.current?.focus(); };
        return (
          <WholesaleDialog name={line.product.name} catalogPrice={line.product.list_price}
            current={line.wholesale ? line.priceUnit : undefined} qty={line.qty}
            unit={isWeighed(line) ? line.product.uom?.name ?? "kg" : "pieza"} currency={setup.store.currency}
            onDone={(price) => { update(setWholesalePrice(cart, line.uuid, price)); close(); }}
            onRemove={() => { update(setWholesalePrice(cart, line.uuid, null)); close(); }}
            onCancel={close} />
        );
      })()}
      {weighing && (
        <WeighDialog product={weighing.product} initial={weighing.qty} currency={setup.store.currency}
          onDone={weighed} onCancel={() => { setWeighing(null); search.current?.focus(); }} />
      )}
    </section>
  );
}
