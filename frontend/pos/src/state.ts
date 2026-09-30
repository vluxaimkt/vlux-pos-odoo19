import { createContext } from "preact";
import { useContext } from "preact/hooks";

import type { ApiClient } from "./api/client";
import type { Employee, Me, RegisterConfig, RegisterState, StoreConfig } from "./api/types";
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

export interface PosContextValue {
  db: PosDb;
  client: ApiClient;
  setup: Setup;
  employees: Employee[];
  employee: Employee | null;
  online: boolean;
  registerState: RegisterState | null;
  taxes: Map<number, TaxInfo>;
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
