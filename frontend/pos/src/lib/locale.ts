/**
 * How money and dates are written: the store's language (from /store/config),
 * so the same register serves any country without code changes.
 */
const FALLBACK = "es-MX";
let current = FALLBACK;

export function setLocale(locale: string | null | undefined): void {
  if (!locale) return void (current = FALLBACK);
  try {
    // An unknown tag would throw at the first format: check it once here.
    new Intl.NumberFormat(locale);
    current = locale;
  } catch {
    current = FALLBACK;
  }
}

export function getLocale(): string {
  return current;
}

export function formatDateTime(value: Date | string | number): string {
  return new Date(value).toLocaleString(current);
}

export function formatTime(value: Date | string | number): string {
  return new Date(value).toLocaleTimeString(current);
}
