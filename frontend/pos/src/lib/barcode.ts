/**
 * The store's barcode nomenclature, read as the Odoo POS reads it
 * (addons/barcodes/static/src/js/barcode_parser.js): scale labels carry a
 * weight ("21.....{NNDDD}") or a price ("23.....{NNNDD}") inside an EAN-13,
 * and the product is found by the code with that part zeroed.
 */
export interface BarcodeRule {
  type: string;
  encoding: string;
  pattern: string;
  sequence: number;
  alias: string | null;
}

export interface Nomenclature {
  upc_ean_conv: string;
  rules: BarcodeRule[];
}

export interface ParsedBarcode {
  /** "product", "weight", "price"… or "error" when no rule matches. */
  type: string;
  code: string;
  /** The code with the encoded number zeroed: what the product carries. */
  baseCode: string;
  /** The weight or price encoded in the label (0 when none). */
  value: number;
}

export function checkDigit(code: string): number {
  const digits = code.split("").reverse();
  digits.shift();
  let even = 0;
  let odd = 0;
  digits.forEach((digit, index) => {
    if (index % 2 === 0) even += Number(digit);
    else odd += Number(digit);
  });
  return (10 - ((even * 3 + odd) % 10)) % 10;
}

const SIZES: Record<string, number> = { ean8: 8, ean13: 13, upca: 12 };

function checkEncoding(code: string, encoding: string): boolean {
  if (encoding === "any") return true;
  return code.length === SIZES[encoding] && /^\d+$/.test(code) && checkDigit(code) === Number(code[code.length - 1]);
}

function sanitizeEan(ean: string): string {
  const padded = "0".repeat(13 - Math.min(13, ean.length)) + ean.slice(0, 13);
  return padded.slice(0, 12) + checkDigit(padded);
}

function escapeRegExp(text: string): string {
  return text.replace(/[\\^$.*+?()[\]{}|]/g, "\\$&");
}

function matchPattern(code: string, pattern: string, encoding: string): { match: boolean; value: number; baseCode: string } {
  let value = 0;
  let baseCode = code;
  let basePattern = pattern;
  const numeric = /\{N*D*\}/.exec(pattern);
  if (numeric) {
    const start = numeric.index;
    const inner = numeric[0].slice(1, -1); // e.g. NNDDD
    const wholeLength = inner.replace(/D/g, "").length;
    const raw = code.substr(start, inner.length);
    const whole = raw.slice(0, wholeLength) || "0";
    const decimals = raw.slice(wholeLength);
    value = parseInt(whole, 10) + (decimals ? parseFloat("0." + decimals) : 0);
    baseCode = code.slice(0, start) + "0".repeat(inner.length) + code.slice(start + inner.length);
    basePattern = pattern.slice(0, start) + "0".repeat(inner.length) + pattern.slice(start + numeric[0].length);
    const digits = baseCode.split("");
    const position = { ean13: 12, ean8: 7, upca: 11 }[encoding as "ean13" | "ean8" | "upca"];
    if (position !== undefined) digits[position] = String(checkDigit(baseCode));
    baseCode = digits.join("");
  }
  // Odoo patterns are regular expressions anchored at the start ("." is any digit).
  const regex = basePattern.split("|").map((part) => (part.startsWith("^") ? part : "^" + part)).join("|");
  let match = false;
  try {
    match = new RegExp(regex).test(baseCode);
  } catch {
    match = baseCode.startsWith(escapeRegExp(basePattern));
  }
  return { match, value, baseCode };
}

export function parseBarcode(barcode: string, nomenclature: Nomenclature | null | undefined): ParsedBarcode {
  const result: ParsedBarcode = { type: "error", code: barcode, baseCode: barcode, value: 0 };
  if (!nomenclature) return result;
  const rules = [...nomenclature.rules].sort((a, b) => a.sequence - b.sequence);
  for (const rule of rules) {
    let code = barcode;
    if (rule.encoding === "ean13" && checkEncoding(barcode, "upca") && ["upc2ean", "always"].includes(nomenclature.upc_ean_conv)) {
      code = "0" + code;
    } else if (rule.encoding === "upca" && checkEncoding(barcode, "ean13") && barcode[0] === "0"
      && ["ean2upc", "always"].includes(nomenclature.upc_ean_conv)) {
      code = code.slice(1, 13);
    }
    if (!checkEncoding(code, rule.encoding)) continue;
    const match = matchPattern(code, rule.pattern, rule.encoding);
    if (!match.match) continue;
    if (rule.type === "alias" && rule.alias) return { ...result, type: "alias", code: rule.alias, baseCode: rule.alias };
    return {
      type: rule.type,
      code,
      value: match.value,
      baseCode: rule.encoding === "ean13" ? sanitizeEan(match.baseCode) : match.baseCode,
    };
  }
  return result;
}
