import type { TaxInfo } from "../sale/pricing";
import type {
  AbonoTicket, CashMove, CreditRow,
  ClosingSummary, Customer, Employee, Envelope, FeedPage, Me, OrderRequest, OrderResult, Product, Quote,
  PosCategory, RegisterState, SaleLine, StaffMember, StaffRole, StoreConfig, TokenInfo,
} from "./types";

export const API_ROOT = "/vlux/api/v1";
const DEFAULT_TIMEOUT_MS = 15_000;

/** The server answered with an error of the contract (`{"ok": false, "error": CODE}`). */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly details?: Record<string, unknown>,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The server could not be reached, timed out, or a proxy answered instead of it. */
export class NetworkError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "NetworkError";
  }
}

/** Worth sending again later: nothing in the request itself is wrong. */
export function isRetryable(error: unknown): boolean {
  if (error instanceof NetworkError) return true;
  if (error instanceof ApiError) {
    return error.status >= 500 || error.status === 429 || (error.code === "CONFLICT" && error.details?.retry === true);
  }
  return false;
}

export interface ApiClientOptions {
  token: string;
  baseUrl?: string;
  timeoutMs?: number;
  fetch?: typeof fetch;
}

export class ApiClient {
  private readonly token: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  /** Employee session (POST /employees/login), sent as X-Vlux-Employee. */
  employeeSession: string | null = null;
  /** Called when the server says the employee must enter the PIN again. */
  onPinRequired: (() => void) | null = null;

  constructor(options: ApiClientOptions) {
    this.token = options.token;
    this.baseUrl = (options.baseUrl ?? "") + API_ROOT;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  async request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Response;
    try {
      response = await this.fetchImpl(this.baseUrl + path, {
        method,
        headers: {
          Authorization: `Bearer ${this.token}`,
          Accept: "application/json",
          ...(this.employeeSession ? { "X-Vlux-Employee": this.employeeSession } : {}),
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      });
    } catch (error) {
      throw new NetworkError(controller.signal.aborted ? "El servidor tardó demasiado." : "Sin conexión con el servidor.");
    } finally {
      clearTimeout(timer);
    }

    let envelope: Envelope<T>;
    try {
      envelope = (await response.json()) as Envelope<T>;
    } catch {
      // A proxy or tunnel page (Cloudflare 502, captive portal) instead of Odoo.
      throw new NetworkError(`Respuesta inesperada del servidor (HTTP ${response.status}).`, response.status);
    }
    if (!envelope || typeof envelope !== "object" || typeof envelope.ok !== "boolean") {
      throw new NetworkError(`Respuesta inesperada del servidor (HTTP ${response.status}).`, response.status);
    }
    if (!envelope.ok) {
      if (envelope.error === "PIN_REQUIRED") this.onPinRequired?.();
      throw new ApiError(
        envelope.error ?? "INTERNAL_ERROR",
        envelope.message ?? "Error del servidor.",
        response.status,
        envelope.details,
        envelope.request_id,
      );
    }
    return envelope.data as T;
  }

  // --- employees module (owner) ---

  staff(registerId: number) {
    return this.request<{ items: StaffMember[] }>("GET", `/registers/${registerId}/staff`);
  }

  addStaff(registerId: number, body: { name: string; role: StaffRole; pin: string }) {
    return this.request<StaffMember>("POST", `/registers/${registerId}/staff/new`, body);
  }

  changeStaff(registerId: number, employeeId: number, body: { name?: string; role?: StaffRole; pin?: string; active?: boolean }) {
    return this.request<StaffMember>("POST", `/registers/${registerId}/staff/${employeeId}`, body);
  }

  // --- phone scanner (vlux_mobile_scanner) ---

  scannerPair(registerId: number, deviceId: string) {
    return this.request<{ pairing_id: number; code: string; status: string; expires_at: string; scanner_url: string; qr_data_uri: string }>(
      "POST", `/registers/${registerId}/scanner/pair`, { device_id: deviceId });
  }

  scannerStatus(registerId: number, pairing: { pairingId: number; deviceId: string }) {
    return this.request<{ pairing_id: number; status: string; last_seen_at: string | null }>(
      "GET", `/registers/${registerId}/scanner/status?pairing_id=${pairing.pairingId}&device_id=${encodeURIComponent(pairing.deviceId)}`);
  }

  scannerRevoke(registerId: number, pairing: { pairingId: number; deviceId: string }) {
    return this.request<{ status: string }>("POST", `/registers/${registerId}/scanner/revoke`,
      { pairing_id: pairing.pairingId, device_id: pairing.deviceId });
  }

  scannerEvents(registerId: number, pairing: { pairingId: number; deviceId: string }) {
    return this.request<{ status: string; items: { request_id: string; barcode: string }[] }>(
      "GET", `/registers/${registerId}/scanner/events?pairing_id=${pairing.pairingId}&device_id=${encodeURIComponent(pairing.deviceId)}`);
  }

  scannerAck(registerId: number, body: {
    device_id: string; request_id: string; status: string; result_code: string; message: string;
    product_id?: number; product_name?: string; unit_price?: number;
  }) {
    return this.request<{ status: string }>("POST", `/registers/${registerId}/scanner/ack`, body);
  }

  posCategories() {
    return this.request<{ items: PosCategory[] }>("GET", "/catalog/pos-categories");
  }

  /** A product picture (binary, not the JSON envelope); null when it has none. */
  async productImage(productId: number, size: 128 | 256 | 512 = 256): Promise<Blob | null> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(`${this.baseUrl}/catalog/products/${productId}/image?size=${size}`, {
        headers: { Authorization: `Bearer ${this.token}` },
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      });
      if (response.status === 404) return null;
      const type = response.headers.get("Content-Type") ?? "";
      if (!response.ok || !type.startsWith("image/")) throw new NetworkError(`Imagen no disponible (HTTP ${response.status}).`, response.status);
      return await response.blob();
    } catch (error) {
      if (error instanceof NetworkError) throw error;
      throw new NetworkError("Sin conexión con el servidor.");
    } finally {
      clearTimeout(timer);
    }
  }

  me() {
    return this.request<Me>("GET", "/me");
  }

  /** Replace this device's token; the answer carries the new one, once. */
  rotateToken() {
    return this.request<TokenInfo & { token: string }>("POST", "/token/rotate", {});
  }

  storeConfig() {
    return this.request<StoreConfig>("GET", "/store/config");
  }

  productsPage(cursor?: string, limit = 500) {
    return this.request<FeedPage<Product>>("GET", `/catalog/products?${feedQuery(cursor, limit)}`);
  }

  customersPage(cursor?: string, limit = 500) {
    return this.request<FeedPage<Customer>>("GET", `/catalog/customers?${feedQuery(cursor, limit)}`);
  }

  taxes() {
    return this.request<{ items: TaxInfo[] }>("GET", "/catalog/taxes");
  }

  employeeLogin(registerId: number, employeeId: number, pin: string) {
    return this.request<{ session: string; expires_at: string; employee: { id: number; name: string; role: string } }>(
      "POST", `/registers/${registerId}/employees/login`, { employee_id: employeeId, pin },
    );
  }

  employeeLogout(registerId: number) {
    return this.request<{ ended: boolean }>("POST", `/registers/${registerId}/employees/logout`, {});
  }

  registerState(registerId: number) {
    return this.request<RegisterState>("GET", `/registers/${registerId}/session`);
  }

  employees(registerId: number) {
    return this.request<{ items: Employee[] }>("GET", `/registers/${registerId}/employees`);
  }

  openSession(registerId: number, body: { opening_cash: number; employee_id?: number; notes?: string }) {
    return this.request<RegisterState>("POST", `/registers/${registerId}/session/open`, body);
  }

  closingSummary(registerId: number) {
    return this.request<ClosingSummary>("GET", `/registers/${registerId}/session/closing`);
  }

  closeSession(
    registerId: number,
    body: {
      session_id: number;
      counted_cash?: number;
      counted?: { payment_method_id: number; amount: number }[];
      employee_id?: number;
      notes?: string;
    },
  ) {
    return this.request<RegisterState>("POST", `/registers/${registerId}/session/close`, body);
  }

  /** Quick create of a product from an unknown barcode (manager with PIN). */
  quickProduct(registerId: number, body: { name: string; barcode: string; list_price: number; taxes_ids?: number[] }) {
    return this.request<Product>("POST", `/registers/${registerId}/products`, body);
  }

  cashMoves(registerId: number) {
    return this.request<{ session_id: number; items: CashMove[] }>("GET", `/registers/${registerId}/session/cash-moves`);
  }

  cashMove(registerId: number, body: { session_id: number; uuid: string; type: "in" | "out"; amount: number; reason: string }) {
    return this.request<CashMove>("POST", `/registers/${registerId}/session/cash-move`, body);
  }

  creditCustomers() {
    return this.request<{ items: CreditRow[] }>("GET", "/credit/customers");
  }

  creditCustomer(partnerId: number) {
    return this.request<CreditRow>("GET", `/credit/customers/${partnerId}`);
  }

  setCredit(partnerId: number, body: { register_id: number; allowed: boolean; limit: number; employee_id?: number }) {
    return this.request<CreditRow>("POST", `/credit/customers/${partnerId}`, body);
  }

  registerAbono(body: {
    uuid: string; register_id: number; partner_id: number; amount: number; payment_method_id: number; employee_id?: number;
  }) {
    return this.request<AbonoTicket>("POST", "/credit/abonos", body);
  }

  createCustomer(body: { name: string; phone?: string; email?: string; vat?: string }) {
    return this.request<Pick<Customer, "id" | "name" | "phone" | "email" | "vat">>("POST", "/customers", body);
  }

  quote(body: { register_id: number; lines: SaleLine[]; partner_id?: number | null }) {
    return this.request<Quote>("POST", "/orders/quote", body);
  }

  createOrder(body: OrderRequest) {
    return this.request<OrderResult>("POST", "/orders", body);
  }

  recentOrders(registerId: number, limit = 40) {
    return this.request<{ items: OrderResult[] }>("GET", `/orders/recent?register_id=${registerId}&limit=${limit}`);
  }

  lookupOrder(reference: string) {
    return this.request<{ items: OrderResult[] }>("GET", `/orders/lookup?reference=${encodeURIComponent(reference)}`);
  }

  refundQuote(body: { register_id: number; order_id: number; lines: { line_id: number; qty: number }[] }) {
    return this.request<{
      order_id: number; amount_refund: number; amount_tax: number;
      lines: { line_id: number; qty: number; amount: number }[];
      paid_with: { payment_method_id: number; name: string; type: string; amount: number }[];
    }>("POST", "/orders/refund/quote", body);
  }

  createRefund(body: {
    uuid: string; register_id: number; order_id: number;
    lines: { line_id: number; qty: number }[]; payments: { payment_method_id: number; amount: number }[];
  }) {
    return this.request<OrderResult>("POST", "/orders/refund", body);
  }

  getOrder(uuid: string) {
    return this.request<OrderResult>("GET", `/orders/${encodeURIComponent(uuid)}`);
  }
}

function feedQuery(cursor: string | undefined, limit: number): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}
