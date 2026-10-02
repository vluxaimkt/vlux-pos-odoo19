import type { CreditRow } from "../api/types";
import { round } from "./money";

// Cents: a balance equal to the limit is still within it (as on the Odoo POS).
const TOLERANCE = 0.005;

/**
 * Who may sell on credit: the encargado or the owner (decision D7), or anyone
 * on a register without employee login. The server re-checks and flags.
 */
export function canSellOnCredit(employeeLogin: boolean, role: string | null | undefined): boolean {
  return !employeeLogin || role === "manager";
}

/**
 * Why `amount` cannot go on this customer's account, or null. Mirrors the
 * Odoo POS: the customer must be authorised and the new balance within the
 * limit (0 = no limit). Offline the register uses its last known balance;
 * the server flags any sale that broke a rule.
 */
export function creditRefusal(row: CreditRow | undefined, name: string, amount: number, money: (n: number) => string): string | null {
  if (!row || !row.allowed) return `${name} no tiene crédito autorizado. El encargado puede autorizarlo en Clientes.`;
  if (row.limit && row.balance + amount > row.limit + TOLERANCE) {
    return `${name} debe ${money(row.balance)} y su límite es ${money(row.limit)}: esta venta de ${money(amount)} lo rebasa. Puede pagar una parte en efectivo.`;
  }
  return null;
}

/** The register's copy of a balance after a sale on credit or an abono. */
export function withBalance(row: CreditRow, balance: number): CreditRow {
  const rounded = round(balance);
  return {
    ...row,
    balance: rounded,
    available: row.limit ? round(row.limit - rounded) : null,
    over_limit: !!row.limit && rounded > row.limit + TOLERANCE,
  };
}
