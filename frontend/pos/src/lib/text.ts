/** Lowercase, without accents: "Café Molido" and "cafe molido" match. */
export function normalize(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** The words a product is found by: its name, code and barcode. */
export function searchWords(...parts: (string | null | undefined)[]): string[] {
  const words = new Set<string>();
  for (const part of parts) {
    if (!part) continue;
    for (const word of normalize(part).split(/[^a-z0-9ñ]+/)) {
      if (word) words.add(word);
    }
  }
  return [...words];
}
