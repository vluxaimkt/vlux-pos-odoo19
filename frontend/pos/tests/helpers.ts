import { ApiClient } from "../src/api/client";
import type { Product } from "../src/api/types";
import { PosDb } from "../src/db/db";

let dbCounter = 0;

export function freshDb(): PosDb {
  dbCounter += 1;
  return new PosDb(`test-${Date.now()}-${dbCounter}`);
}

export type Handler = (method: string, path: string, body: unknown) => { status?: number; json?: unknown; text?: string } | Error;

/** An ApiClient whose fetch is `handler`; records every call. */
export function fakeClient(handler: Handler) {
  const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    const path = url.replace("/vlux/api/v1", "");
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method: init.method ?? "GET", path, body, headers: init.headers as Record<string, string> });
    const answer = handler(init.method ?? "GET", path, body);
    if (answer instanceof Error) throw answer;
    const text = answer.text ?? JSON.stringify(answer.json);
    return new Response(text, { status: answer.status ?? 200, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch;
  return { client: new ApiClient({ token: "tok", fetch: fetchImpl }), calls };
}

export const ok = (data: unknown) => ({ json: { ok: true, data, request_id: "r" } });
export const fail = (status: number, error: string, details?: Record<string, unknown>) => ({
  status,
  json: { ok: false, error, message: error, details, request_id: "r" },
});

export function product(id: number, name: string, extra: Partial<Product> = {}): Product {
  return {
    id, template_id: id, name, barcode: null, default_code: null, list_price: 10, tax_ids: [],
    pos_category_ids: [], uom: { id: 1, name: "Unidades" }, active: true, available_in_pos: true,
    sale_ok: true, image_version: null, sync_date: "2026-09-28T00:00:00.000000Z", ...extra,
  };
}
