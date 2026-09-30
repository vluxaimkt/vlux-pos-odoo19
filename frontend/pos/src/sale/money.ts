/** Round like the server (HALF-UP) to the currency's decimals. */
export function round(amount: number, decimals = 2): number {
  const factor = 10 ** decimals;
  const scaled = Math.abs(amount) * factor;
  // Guard against binary noise: 1.005 * 100 = 100.49999999999999.
  const rounded = Math.round(scaled + 1e-7) / factor;
  return amount < 0 ? -rounded : rounded;
}

export function sum(values: number[], decimals = 2): number {
  return round(values.reduce((total, value) => total + value, 0), decimals);
}
