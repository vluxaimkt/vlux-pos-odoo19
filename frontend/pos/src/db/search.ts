import { normalize, searchWords } from "../lib/text";
import type { PosDb, ProductRow } from "./db";

function sellable(product: ProductRow): boolean {
  return product.active && product.available_in_pos && product.sale_ok;
}

/**
 * Products to show in the grid: those of a POS category (or all), sellable
 * only, by name. `allowed` limits them to the register's categories.
 */
export async function browseProducts(
  db: PosDb, categoryId: number | null, allowed: number[] | null, limit = 120,
): Promise<{ items: ProductRow[]; more: boolean }> {
  const rows = categoryId === null
    ? await db.products.toArray()
    : await db.products.where("pos_category_ids").equals(categoryId).toArray();
  const items = rows
    .filter((product) => sellable(product) && (!allowed || product.pos_category_ids.some((id) => allowed.includes(id))))
    .sort((a, b) => normalize(a.name).localeCompare(normalize(b.name)));
  return { items: items.slice(0, limit), more: items.length > limit };
}

/** The product a scanner read, if the register has it and it is sold. */
export async function productByBarcode(db: PosDb, barcode: string): Promise<ProductRow | undefined> {
  const code = barcode.trim();
  if (!code) return undefined;
  const found = await db.products.where("barcode").equals(code).toArray();
  return found.find(sellable);
}

/**
 * Products whose words start with every word typed ("cafe mol" finds
 * "Café Molido 500 g"), sellable only, by name.
 */
export async function searchProducts(db: PosDb, query: string, limit = 50): Promise<ProductRow[]> {
  const words = searchWords(query);
  const first = words[0];
  if (!first) return [];
  const rest = words.slice(1);
  const candidates = await db.products.where("words").startsWith(first).distinct().toArray();
  return candidates
    .filter((product) => sellable(product) && rest.every((word) => product.words.some((own) => own.startsWith(word))))
    .sort((a, b) => normalize(a.name).localeCompare(normalize(b.name)))
    .slice(0, limit);
}
