import type { ApiClient } from "../api/client";
import type { TokenInfo } from "../api/types";
import { getMeta, type PosDb, setMeta } from "../db/db";

export const META_TOKEN_INFO = "token_info";
/** Renew when less than this is left (tokens for registers last 30 days). */
export const RENEW_BEFORE_MS = 15 * 24 * 60 * 60_000;

export function needsRenewal(info: Pick<TokenInfo, "renewable" | "expires_at"> | undefined, now: number): boolean {
  if (!info?.renewable || !info.expires_at) return false;
  return Date.parse(info.expires_at) - now < RENEW_BEFORE_MS;
}

/**
 * Renew the register's token when it is getting old (POST /token/rotate).
 *
 * The new token is stored before anything else uses it. If the answer is
 * lost on the way, the old token keeps working for a day on the server and
 * the next attempt simply asks again. Returns the new token, or null when
 * nothing had to be done.
 */
export async function renewIfDue(client: ApiClient, db: PosDb, now = Date.now()): Promise<string | null> {
  const info = await getMeta<TokenInfo>(db, META_TOKEN_INFO);
  if (!needsRenewal(info, now)) return null;
  const { token, ...fresh } = await client.rotateToken();
  await db.transaction("rw", db.meta, async () => {
    await setMeta(db, "token", token);
    await setMeta(db, META_TOKEN_INFO, fresh);
  });
  return token;
}
