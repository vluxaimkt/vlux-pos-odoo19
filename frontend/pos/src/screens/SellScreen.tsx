import { useEffect, useRef, useState } from "preact/hooks";

import { isRetryable } from "../api/client";
import type { OrderRequest } from "../api/types";
import { getMeta, type ProductRow, setMeta } from "../db/db";
import { productByBarcode, searchProducts } from "../db/search";
import { formatMoney } from "../lib/money";
import { addProduct, type Cart, emptyCart, itemCount, removeLine, setQty } from "../sale/cart";
import { fromQuote, localPricing, type Pricing } from "../sale/pricing";
import { META_CART, usePos } from "../state";
import { PayScreen } from "./PayScreen";
import { ReceiptScreen } from "./ReceiptScreen";
import { explain } from "./SetupScreen";

const newId = () => crypto.randomUUID();

type Stage =
  | { name: "cart" }
  | { name: "pay"; pricing: Pricing }
  | { name: "receipt"; order: OrderRequest; pricing: Pricing };

/** Ring up a sale: scan or search, adjust the cart, charge, print the ticket. */
export function SellScreen() {
  const { db, client, setup, online, taxes } = usePos();
  const [cart, setCart] = useState<Cart | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProductRow[]>([]);
  const [stage, setStage] = useState<Stage>({ name: "cart" });
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const search = useRef<HTMLInputElement>(null);

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
    const id = setTimeout(() => setNotice(null), 4000);
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

  function add(product: ProductRow) {
    if (!cart) return;
    update(addProduct(cart, product, newId));
    setQuery("");
    setNotice(null);
    search.current?.focus();
  }

  /** A barcode scanner types the code and presses Enter. */
  async function onEnter(event: KeyboardEvent) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const code = query.trim();
    if (!code) return;
    const product = await productByBarcode(db, code);
    if (product) return add(product);
    if (results.length === 1 && results[0]) return add(results[0]);
    setNotice(`No se encontró "${code}" en esta caja.`);
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
            lines: cart.lines.map((line) => ({ uuid: line.uuid, product_id: line.product.id, qty: line.qty })),
          });
          pricing = fromQuote(cart, quote);
        } catch (error) {
          if (!isRetryable(error)) throw error;
        }
      }
      pricing ??= localPricing(cart, taxes, setup.register.use_pricelist);
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

  function finished(order: OrderRequest, pricing: Pricing) {
    update(emptyCart(newId));
    setStage({ name: "receipt", order, pricing });
  }

  if (!cart) return null;
  if (stage.name === "pay") {
    return <PayScreen cart={cart} pricing={stage.pricing} onBack={() => setStage({ name: "cart" })} onPaid={finished} />;
  }
  if (stage.name === "receipt") {
    return <ReceiptScreen order={stage.order} pricing={stage.pricing} onNext={() => setStage({ name: "cart" })} />;
  }

  const estimate = localPricing(cart, taxes, setup.register.use_pricelist);
  return (
    <section class="p-3 grid gap-3 lg:grid-cols-[1fr_24rem]">
      <div class="flex flex-col gap-3">
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
        {notice && <div role="alert" class="alert alert-warning">{notice}</div>}
        <ul class="list bg-base-100 rounded-box">
          {results.map((product) => (
            <li key={product.id}>
              <button class="list-row w-full text-left items-center hover:bg-base-200" onClick={() => add(product)}>
                <div class="list-col-grow">
                  <div>{product.name}</div>
                  <div class="text-xs opacity-60 font-mono">{product.barcode ?? product.default_code ?? ""}</div>
                </div>
                <div class="font-semibold">{formatMoney(product.list_price, setup.store.currency)}</div>
              </button>
            </li>
          ))}
        </ul>
        {query && !results.length && <p class="opacity-60">Sin resultados en esta caja.</p>}
      </div>

      <aside class="card bg-base-100 shadow">
        <div class="card-body gap-2 p-4">
          <h2 class="card-title">Venta <span class="badge">{itemCount(cart)}</span></h2>
          {!cart.lines.length && <p class="opacity-60">Escanea un producto para empezar.</p>}
          <ul class="flex flex-col gap-2 max-h-[55vh] overflow-y-auto">
            {cart.lines.map((line) => (
              <li key={line.uuid} class="flex items-center gap-2">
                <div class="flex-1 min-w-0">
                  <div class="truncate">{line.product.name}</div>
                  <div class="text-xs opacity-60">{formatMoney(line.product.list_price, setup.store.currency)} c/u</div>
                </div>
                <div class="join">
                  <button class="btn btn-sm join-item" aria-label="Menos" onClick={() => update(setQty(cart, line.uuid, line.qty - 1))}>−</button>
                  <span class="btn btn-sm join-item pointer-events-none">{line.qty}</span>
                  <button class="btn btn-sm join-item" aria-label="Más" onClick={() => update(setQty(cart, line.uuid, line.qty + 1))}>+</button>
                </div>
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
    </section>
  );
}
