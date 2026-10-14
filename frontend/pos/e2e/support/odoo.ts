import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/** What tools/e2e/pwa_e2e_env.py generated for this run (synthetic data, secrets included). */
export interface Fixture {
  database: string;
  register: { id: number; name: string; difference_limit: number };
  cashier: { id: number; name: string; pin: string };
  manager: { id: number; name: string; pin: string };
  products: { id: number; name: string; price: number; barcode: string }[];
  payment_methods: { cash: number; card: number };
  tokens: Record<string, string>;
  api_key: string;
  refunds_need_manager: boolean;
}

export function loadFixture(): Fixture {
  const path = resolve(process.env.VLUX_E2E_FIXTURE || "e2e/.fixture.json");
  return JSON.parse(readFileSync(path, "utf-8")) as Fixture;
}

type Domain = (string | number | boolean | null | (string | number | boolean | null)[])[][];

/**
 * Reads what Odoo stored, over Odoo 19's JSON-2 API with the run's API key:
 * the browser never sees it, and it only reads.
 */
export class OdooReader {
  constructor(private readonly baseURL: string, private readonly fixture: Fixture) {}

  async call<T>(model: string, method: string, body: Record<string, unknown>): Promise<T> {
    const response = await fetch(`${this.baseURL}/json/2/${model}/${method}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `bearer ${this.fixture.api_key}`,
        "X-Odoo-Database": this.fixture.database,
      },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    if (!response.ok) throw new Error(`Odoo ${model}.${method} → ${response.status}: ${text.slice(0, 500)}`);
    return JSON.parse(text) as T;
  }

  searchRead<T = Record<string, unknown>>(model: string, domain: Domain, fields: string[], order = "id"): Promise<T[]> {
    return this.call<T[]>(model, "search_read", { domain, fields, order });
  }

  /** The register's latest session. */
  async lastSession(): Promise<Session | undefined> {
    const rows = await this.searchRead<Session>("pos.session", [["config_id", "=", this.fixture.register.id]],
      ["id", "name", "state", "cash_register_balance_start", "cash_register_balance_end_real", "cash_register_difference"],
      "id desc");
    return rows[0];
  }

  /** Every order (sale or return) with one of these uuids: a duplicate shows up as two rows. */
  ordersByUuid(uuids: string[]): Promise<Order[]> {
    return this.searchRead<Order>("pos.order", [["uuid", "in", uuids]],
      ["id", "uuid", "name", "pos_reference", "state", "amount_total", "amount_paid", "amount_return", "session_id",
        "employee_id", "vlux_api_employee_verified", "lines", "payment_ids", "is_refund"]);
  }

  ordersOfSession(sessionId: number): Promise<Order[]> {
    return this.searchRead<Order>("pos.order", [["session_id", "=", sessionId]],
      ["id", "uuid", "name", "pos_reference", "state", "amount_total", "is_refund", "employee_id"]);
  }

  lines(ids: number[]): Promise<OrderLine[]> {
    return this.searchRead<OrderLine>("pos.order.line", [["id", "in", ids]],
      ["id", "product_id", "qty", "price_subtotal_incl", "refunded_orderline_id", "refunded_qty"]);
  }

  payments(ids: number[]): Promise<Payment[]> {
    return this.searchRead<Payment>("pos.payment", [["id", "in", ids]], ["id", "payment_method_id", "amount", "is_change"]);
  }
}

export type Many2one = [number, string] | false;

export interface Session {
  id: number;
  name: string;
  state: string;
  cash_register_balance_start: number;
  cash_register_balance_end_real: number;
  cash_register_difference: number;
}

export interface Order {
  id: number;
  uuid: string;
  name: string;
  pos_reference: string | false;
  state: string;
  amount_total: number;
  amount_paid: number;
  amount_return: number;
  session_id: Many2one;
  employee_id: Many2one;
  vlux_api_employee_verified: boolean;
  lines: number[];
  payment_ids: number[];
  is_refund: boolean;
}

export interface OrderLine {
  id: number;
  product_id: Many2one;
  qty: number;
  price_subtotal_incl: number;
  refunded_orderline_id: Many2one;
  refunded_qty: number;
}

export interface Payment {
  id: number;
  payment_method_id: Many2one;
  amount: number;
  is_change: boolean;
}
