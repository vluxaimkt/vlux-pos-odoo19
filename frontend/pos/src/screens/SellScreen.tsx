import { useEffect, useState } from "preact/hooks";

import type { ProductRow } from "../db/db";
import { productByBarcode, searchProducts } from "../db/search";
import { formatMoney } from "../lib/money";
import { usePos } from "../state";

/**
 * Sale screen, first cut: finds products in the local catalog by name, code
 * or barcode, without the network. Cart, payment and ticket come next.
 */
export function SellScreen() {
  const { db, setup } = usePos();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProductRow[]>([]);

  useEffect(() => {
    let current = true;
    void (async () => {
      const exact = await productByBarcode(db, query);
      const found = exact ? [exact] : await searchProducts(db, query);
      if (current) setResults(found);
    })();
    return () => {
      current = false;
    };
  }, [db, query]);

  return (
    <section class="p-4 flex flex-col gap-3">
      <input
        class="input input-bordered input-lg w-full"
        type="search"
        placeholder="Escanea o busca un producto"
        autofocus
        value={query}
        onInput={(event) => setQuery(event.currentTarget.value)}
      />
      <ul class="list bg-base-100 rounded-box">
        {results.map((product) => (
          <li key={product.id} class="list-row items-center">
            <div class="list-col-grow">
              <div>{product.name}</div>
              <div class="text-xs opacity-60 font-mono">{product.barcode ?? product.default_code ?? ""}</div>
            </div>
            <div class="font-semibold">{formatMoney(product.list_price, setup.store.currency)}</div>
          </li>
        ))}
      </ul>
      {query && !results.length && <p class="opacity-60">Sin resultados en esta caja.</p>}
      <div role="status" class="alert alert-info">Carrito, cobro y ticket: siguiente entrega.</div>
    </section>
  );
}
