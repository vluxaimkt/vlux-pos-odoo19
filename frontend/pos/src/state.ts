import { createContext } from "preact";
import { useContext } from "preact/hooks";

import type { ApiClient } from "./api/client";
import type { CreditRow, Employee, Me, RegisterConfig, RegisterState, StoreConfig } from "./api/types";
import type { PosDb } from "./db/db";
import type { TaxInfo } from "./sale/pricing";

/** What pairing this device with a store and register left in the local database. */
export interface Setup {
  me: Me;
  store: StoreConfig;
  register: RegisterConfig;
}

export const META_TOKEN = "token";
export const META_SETUP = "setup";
export const META_EMPLOYEES = "employees";
export const META_TAXES = "taxes";
export const META_CART = "cart";
export const META_CREDIT = "credit";

export interface PosContextValue {
  db: PosDb;
  client: ApiClient;
  setup: Setup;
  employees: Employee[];
  employee: Employee | null;
  online: boolean;
  registerState: RegisterState | null;
  taxes: Map<number, TaxInfo>;
  /** Customers with credit or a balance, by partner id (last known, kept offline). */
  credit: Map<number, CreditRow>;
  /** Whether the person at the register may sell on credit (D7). */
  canSellOnCredit: boolean;
  /** Whether the person at the register may authorise credit and set limits. */
  canAuthorizeCredit: boolean;
  /** Whether the person at the register may add and edit products (the server re-checks). */
  canEditCatalog: boolean;
  saveCredit(row: CreditRow): void;
  refreshCredit(): Promise<void>;
  setEmployee(employee: Employee | null): void;
  /** Try to send the queued sales now (after a sale, or from the queue screen). */
  flushNow(): Promise<void>;
  forget(): Promise<void>;
}

export const PosContext = createContext<PosContextValue | null>(null);

export function usePos(): PosContextValue {
  const value = useContext(PosContext);
  if (!value) throw new Error("usePos fuera de PosContext");
  return value;
}
