import { ApiError, type ApiClient, isRetryable, NetworkError } from "../api/client";
import type { OrderRequest } from "../api/types";
import type { OutboxRow, PosDb } from "../db/db";

const MAX_BACKOFF_MS = 60_000;

/** Wait before retry number `attempts`: 2 s, 4 s, 8 s… up to a minute. */
export function backoff(attempts: number): number {
  return Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.max(1, attempts));
}

/**
 * Keep a sale locally before anything is sent. The uuid in the body is what
 * makes a resend harmless: the server records it once (POST /orders).
 */
export async function enqueueSale(db: PosDb, body: OrderRequest, now = Date.now()): Promise<OutboxRow> {
  const row: OutboxRow = {
    uuid: body.uuid,
    body,
    status: "pending",
    attempts: 0,
    createdAt: new Date(now).toISOString(),
    nextAttemptAt: now,
  };
  await db.outbox.add(row);
  return row;
}

export interface FlushResult {
  sent: number;
  attention: number;
  pending: number;
  offline: boolean;
}

/**
 * Send the pending sales, oldest first.
 *
 * - Recorded (or already recorded: `duplicate`) → `sent`, with the server's order.
 * - Refused by the contract (409 mismatch, no open session, 400) → `attention`:
 *   kept, shown to the cashier, never dropped.
 * - Network down, timeout, 5xx, 429 → stays `pending` with a backoff; on a
 *   network failure the rest of the queue waits for the next flush.
 */
export async function flushOutbox(client: ApiClient, db: PosDb, now = Date.now()): Promise<FlushResult> {
  const result: FlushResult = { sent: 0, attention: 0, pending: 0, offline: false };
  const due = (await db.outbox.where("status").equals("pending").sortBy("createdAt")).filter(
    (row) => row.nextAttemptAt <= now,
  );
  for (const row of due) {
    try {
      const order = await client.createOrder(row.body);
      await db.outbox.update(row.uuid, { status: "sent", result: order, lastError: undefined, attempts: row.attempts + 1 });
      result.sent += 1;
    } catch (error) {
      const attempts = row.attempts + 1;
      if (isRetryable(error)) {
        await db.outbox.update(row.uuid, {
          attempts,
          nextAttemptAt: now + backoff(attempts),
          lastError: describe(error),
        });
        result.pending += 1;
        if (error instanceof NetworkError) {
          result.offline = true;
          break;
        }
      } else {
        await db.outbox.update(row.uuid, { status: "attention", attempts, lastError: describe(error) });
        result.attention += 1;
      }
    }
  }
  return result;
}

/** Put a sale that needed attention back in the queue (e.g. after opening the register). */
export async function retrySale(db: PosDb, uuid: string, now = Date.now()): Promise<void> {
  await db.outbox.update(uuid, { status: "pending", nextAttemptAt: now });
}

function describe(error: unknown): OutboxRow["lastError"] {
  if (error instanceof ApiError) return { code: error.code, message: error.message, details: error.details };
  if (error instanceof NetworkError) return { code: "NETWORK", message: error.message };
  return { code: "UNKNOWN", message: error instanceof Error ? error.message : String(error) };
}
