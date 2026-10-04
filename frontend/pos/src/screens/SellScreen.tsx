import { useEffect, useRef, useState } from "preact/hooks";

import { isRetryable } from "../api/client";
import type { OrderRequest } from "../api/types";
import { getMeta, productRow, type ProductRow, setMeta } from "../db/db";
import { productByBarcode, searchProducts } from "../db/search";
import { parseBarcode } from "../lib/barcode";
import { formatMoney } from "../lib/money";
import {
  addProduct, type AddOptions, type Cart, emptyCart, isWeighed, itemCount, qtyLabel, removeLine, setCustomer, setQty, unitPrice,
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

const newId = () => crypto.randomUUID();

type Stage =
  | { name: "cart" }
  | { name: "customer" }
  | { name: "pay"; pricing: Pricing }
  | { name: "receipt"; order: OrderRequest; pricing: Pricing; credit: CreditTicket | null };

/** Ring up a sale: scan or search, adjust the cart, charge, print the ticket. */
export function SellScreen() {
  const { db, client, setup, online, taxes, credit, employee, registerState } = usePos();
  const [cart, setCart] = useState<Cart | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProductRow[]>([]);
  const [stage, setStage] = useState<Stage>({ name: "cart" });
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** A product sold by weight waiting for its kilos (new line, or re-weighing a line). */
  const [weighing, setWeighing] = useState<{ product: ProductRow | Cart["lines"][number]["product"]; lineUuid?: string; qty?: number } | null>(null);
  /** A scanned code the store does not know: a manager may add it on the spot. */
  const [unknown, setUnknown] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const canCreate = online && (!registerState?.employee_login || employee?.role === "manager");
  const search = useRef<HTMLInputElement>(null);
  const rounding = setup.store.tax_rounding;

  // The cart survives a reload of the app (power cut, accidental refresh).
  useEffect(() => {
    void getMeta<Cart>(db, META_CART).then((saved) => setCart(saved ?? emptyCart(newId)));
  }, [db]);

  function update(next: Cart) {
    setCart(next);
    void setMeta(db, META_CART, next);
  }

  // A notice clears itself; typing or scanning again also clears it.
  useEffect(() => {
    if (!notice) return;
    // Longer when it offers to add the unknown product.
    const id = setTimeout(() => { setNotice(null); setUnknown(null); }, unknown && canCreate ? 15000 : 4000);
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
  }, [db, query]);

  function add(product: ProductRow, qty?: number, options: AddOptions = {}) {
    if (!cart) return;
    setQuery("");
    setNotice(null);
    // Sold by weight with no weight yet: ask for the kilos.
    if (product.to_weight && qty === undefined && options.priceUnit === undefined) {
      setWeighing({ product });
      return;
    }
    update(addProduct(cart, product, newId, qty ?? 1, options));
    search.current?.focus();
  }

  function weighed(kilos: number) {
    if (!cart || !weighing) return;
    update(weighing.lineUuid ? setQty(cart, weighing.lineUuid, kilos) : addProduct(cart, weighing.product, newId, kilos));
    setWeighing(null);
    search.current?.focus();
  }

  /** A barcode scanner types the code and presses Enter. */
  async function onEnter(event: KeyboardEvent) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const code = query.trim();
    if (!code) return;
    // A scale label carries the weight or the price; the product has the code with that part zeroed.
    const parsed = parseBarcode(code, setup.store.barcode_nomenclature);
    if ((parsed.type === "weight" || parsed.type === "price") && parsed.value > 0) {
      const labelled = (await productByBarcode(db, parsed.baseCode)) ?? (await productByBarcode(db, code));
      if (labelled) {
        return parsed.type === "weight" ? add(labelled, parsed.value) : add(labelled, 1, { priceUnit: parsed.value });
      }
    }
    const product = await productByBarcode(db, code);
    if (product) return add(product);
    if (results.length === 1 && results[0]) return add(results[0]);
    setNotice(`No se encontró "${code}" en esta caja.`);
    // A scale label of an unknown product is not a product code: set that product up in Odoo.
    setUnknown(parsed.type === "weight" || parsed.type === "price" ? null : code);
    setQuery("");
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
  return (
    <section class="p-3 grid gap-3 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_24rem]">
      <div class="flex flex-col gap-3 min-w-0">
        <input
          ref={search}
          class="input input-lg w-full"
          type="search"
          placeholder="Escanea o busca un producto"
          autofocus
          autocomplete="off"
          value={query}
          onInput={(event) => {
            setQuery(event.currentTarget.value);
            setNotice(null);
          }}
          onKeyDown={(event) => void onEnter(event)}
        />
        {notice && (
          <div role="alert" class="alert alert-warning">
            <span class="flex-1">{notice}</span>
            {unknown && canCreate && <button class="btn btn-sm" onClick={() => setCreating(true)}>Dar de alta</button>}
          </div>
        )}
        <ProductGrid searched={query.trim() ? results : null} onPick={(product) => add(product)} />
        {query && !results.length && <p class="opacity-60">Sin resultados en esta caja.</p>}
      </div>

      <aside class="card bg-base-100 shadow min-w-0">
        <div class="card-body gap-2 p-4">
          <h2 class="card-title">Venta <span class="badge">{itemCount(cart)}</span></h2>
          <div class="flex items-start gap-2 text-sm">
            {cart.customer ? (
              <div class="flex-1">
                <div>Cliente: <strong>{cart.customer.name}</strong></div>
                <CreditLine row={credit.get(cart.customer.id)} money={(n) => formatMoney(n, setup.store.currency)} />
              </div>
            ) : (
              <span class="flex-1 opacity-70">Sin cliente</span>
            )}
            <button class="btn btn-xs" onClick={() => setStage({ name: "customer" })}>{cart.customer ? "Cambiar" : "Elegir cliente"}</button>
            {cart.customer && <button class="btn btn-xs btn-ghost" aria-label="Quitar cliente" onClick={() => update(setCustomer(cart, null))}>✕</button>}
          </div>
          {!cart.lines.length && <p class="opacity-60">Escanea un producto para empezar.</p>}
          <ul class="flex flex-col gap-2 max-h-[55vh] overflow-y-auto">
            {cart.lines.map((line) => (
              <li key={line.uuid} class="flex items-center gap-2">
                <div class="flex-1 min-w-0">
                  <div class="truncate">{line.product.name}</div>
                  <div class="text-xs opacity-60">
                    {isWeighed(line)
                      ? `${qtyLabel(line.qty, line.product.uom?.name ?? "kg")} x ${formatMoney(unitPrice(line), setup.store.currency)} = ${formatMoney(unitPrice(line) * line.qty, setup.store.currency)}`
                      : `${formatMoney(unitPrice(line), setup.store.currency)} c/u`}
                    {line.priceFromBarcode && " · precio de etiqueta"}
                  </div>
                </div>
                {isWeighed(line) ? (
                  <button class="btn btn-sm" onClick={() => setWeighing({ product: line.product, lineUuid: line.uuid, qty: line.qty })}>Pesar</button>
                ) : (
                  <div class="join">
                    <button class="btn btn-sm join-item" aria-label="Menos" onClick={() => update(setQty(cart, line.uuid, line.qty - 1))}>−</button>
                    <span class="btn btn-sm join-item pointer-events-none">{line.qty}</span>
                    <button class="btn btn-sm join-item" aria-label="Más" onClick={() => update(setQty(cart, line.uuid, line.qty + 1))}>+</button>
                  </div>
                )}
                <button class="btn btn-ghost btn-sm" aria-label="Quitar" onClick={() => update(removeLine(cart, line.uuid))}>✕</button>
              </li>
            ))}
          </ul>
          <div class="divider my-1" />
          <div class="flex justify-between text-2xl font-bold">
            <span>Total</span>
            <span>{formatMoney(estimate.total, setup.store.currency)}</span>
          </div>
          {!estimate.exact && <p class="text-xs opacity-70">El total final lo confirma el servidor al cobrar.</p>}
          <button class="btn btn-primary btn-lg" disabled={!cart.lines.length || busy} onClick={() => void charge()}>
            {busy ? <span class="loading loading-spinner" /> : "Cobrar"}
          </button>
          {cart.lines.length > 0 && (
            <button class="btn btn-ghost btn-sm" onClick={() => { if (confirm("¿Cancelar esta venta?")) update(emptyCart(newId)); }}>
              Cancelar venta
            </button>
          )}
        </div>
      </aside>
      {creating && unknown && (
        <QuickProductDialog barcode={unknown}
          onCancel={() => { setCreating(false); search.current?.focus(); }}
          onCreated={async (product) => {
            // Into the local catalog now; the next sync brings the same row.
            const row = productRow(product);
            await db.products.put(row);
            setCreating(false);
            setUnknown(null);
            add(row);
          }} />
      )}
      {weighing && (
        <WeighDialog product={weighing.product} initial={weighing.qty} currency={setup.store.currency}
          onDone={weighed} onCancel={() => { setWeighing(null); search.current?.focus(); }} />
      )}
    </section>
  );
}
