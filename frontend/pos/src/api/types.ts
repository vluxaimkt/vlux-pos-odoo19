// The parts of the VLUX API v1 contract the register uses (docs/API_V1.md).
// v1 only grows: fields may be added, never removed or changed.

export interface Envelope<T> {
  ok: boolean;
  data?: T;
  error?: string;
  message?: string;
  details?: Record<string, unknown>;
  request_id?: string;
}

export interface TokenInfo {
  name: string;
  prefix: string;
  scopes: string[];
  /** The register this token is bound to; null when it serves any register. */
  register_id: number | null;
  expires_at: string | null;
  renewable: boolean;
}

export interface Me {
  api_version: string;
  token: TokenInfo;
  user: { id: number; name: string; login: string };
  company: { id: number; name: string; country: string | null; currency: string };
  server_time: string;
}

export interface PaymentMethod {
  id: number;
  name: string;
  type: "cash" | "bank" | "pay_later" | string;
  is_cash: boolean;
}

export interface RegisterConfig {
  id: number;
  name: string;
  currency_id: number;
  pricelist_id: number | null;
  use_pricelist: boolean;
  tax_display: string;
  payment_methods: PaymentMethod[];
  receipt_header: string | null;
  receipt_footer: string | null;
}

export interface Currency {
  id: number;
  name: string;
  symbol: string;
  decimal_places: number;
  rounding: number;
}

export interface StoreConfig {
  company: {
    id: number;
    name: string;
    vat: string | null;
    fiscal_regime: string | null;
    receipt_legend: string | null;
    street: string | null;
    city: string | null;
    zip: string | null;
    phone: string | null;
    timezone: string;
  };
  currency: Currency;
  registers: RegisterConfig[];
}

export interface Product {
  id: number;
  template_id: number;
  name: string;
  barcode: string | null;
  default_code: string | null;
  list_price: number;
  tax_ids: number[];
  pos_category_ids: number[];
  uom: { id: number; name: string };
  active: boolean;
  available_in_pos: boolean;
  sale_ok: boolean;
  image_version: string | null;
  sync_date: string;
}

export interface Customer {
  id: number;
  name: string;
  phone: string | null;
  email: string | null;
  vat: string | null;
  barcode: string | null;
  active: boolean;
  sync_date: string;
}

export interface FeedPage<T> {
  items: T[];
  deleted: number[];
  next_cursor: string;
  has_more: boolean;
  server_time: string;
}

export interface Session {
  id: number;
  name: string;
  state: "opening_control" | "opened" | "closing_control" | "closed";
  opened_at: string | null;
  closed_at: string | null;
  opening_cash: number;
  employee_id: number | null;
  user_id: number | null;
}

export interface RegisterState {
  register_id: number;
  name: string;
  employee_login: boolean;
  cash_control: boolean;
  max_difference: number | null;
  session: Session | null;
  already_open?: boolean;
}

export interface Employee {
  id: number;
  name: string;
  role: "manager" | "cashier" | "minimal";
  user_id: number | null;
  pin_sha1: string | null;
  barcode_sha1: string | null;
}

export interface SaleLine {
  uuid?: string;
  product_id: number;
  qty: number;
  price_unit?: number;
}

export interface QuoteLine {
  uuid: string | null;
  product_id: number;
  name: string;
  qty: number;
  price_unit: number;
  catalog_price: number;
  price_overridden: boolean;
  tax_ids: number[];
  price_subtotal: number;
  price_subtotal_incl: number;
}

export interface Quote {
  pricelist_id: number | null;
  amount_untaxed: number;
  amount_tax: number;
  amount_total: number;
  lines: QuoteLine[];
}

export interface OrderRequest {
  uuid: string;
  register_id: number;
  employee_id?: number;
  partner_id?: number | null;
  session_id?: number;
  lines: SaleLine[];
  payments: { payment_method_id: number; amount: number }[];
  expected_total?: number;
  created_at?: string;
}

export interface OrderResult {
  id: number;
  uuid: string;
  name: string;
  pos_reference: string | null;
  tracking_number: string | null;
  state: string;
  register_id: number;
  session_id: number;
  employee_id: number | null;
  partner_id: number | null;
  date_order: string;
  amount_total: number;
  amount_tax: number;
  amount_paid: number;
  change: number;
  price_overridden: boolean;
  duplicate?: boolean;
  lines: { uuid: string; product_id: number; name: string; qty: number; price_unit: number; price_subtotal_incl: number }[];
  payments: { payment_method_id: number; name: string; amount: number; is_change: boolean }[];
}

export interface ClosingSummary {
  session: Session;
  max_difference: number | null;
  orders: { count: number; amount: number };
  cash: {
    payment_method_id: number;
    name: string;
    opening: number;
    sales: number;
    moves: { name: string; amount: number }[];
    expected: number;
  } | null;
  other_methods: { payment_method_id: number; name: string; type: string; expected: number; count: number }[];
}
