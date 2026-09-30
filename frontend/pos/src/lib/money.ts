import type { Currency } from "../api/types";

const FALLBACK: Pick<Currency, "name" | "decimal_places"> = { name: "MXN", decimal_places: 2 };

export function formatMoney(amount: number, currency?: Pick<Currency, "name" | "decimal_places">): string {
  const { name, decimal_places } = currency ?? FALLBACK;
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: name,
    minimumFractionDigits: decimal_places,
    maximumFractionDigits: decimal_places,
  }).format(amount);
}
