/**
 * Slows down PIN guessing at the register: after `maxFailures` wrong PINs in
 * a row the keypad locks, first for `baseLockMs`, doubling on each further
 * lock up to `maxLockMs`. A right PIN resets it. The state is plain data so
 * it can be stored (it survives a reload of the app).
 *
 * This protects the keypad, not the hashes: PIN hashes are on the device for
 * offline login, as in the Odoo POS (see docs/POS_PWA.md, F35).
 */
export interface GuardState {
  failures: number;
  locks: number;
  lockedUntil: number;
}

export interface GuardPolicy {
  maxFailures: number;
  baseLockMs: number;
  maxLockMs: number;
}

export const DEFAULT_POLICY: GuardPolicy = { maxFailures: 5, baseLockMs: 30_000, maxLockMs: 15 * 60_000 };
export const OPEN: GuardState = { failures: 0, locks: 0, lockedUntil: 0 };

export function lockedFor(state: GuardState, now: number): number {
  return Math.max(0, state.lockedUntil - now);
}

export function recordFailure(state: GuardState, now: number, policy = DEFAULT_POLICY): GuardState {
  const failures = state.failures + 1;
  if (failures < policy.maxFailures) return { ...state, failures };
  const locks = state.locks + 1;
  const duration = Math.min(policy.maxLockMs, policy.baseLockMs * 2 ** (locks - 1));
  return { failures: 0, locks, lockedUntil: now + duration };
}

export function recordSuccess(): GuardState {
  return { ...OPEN };
}
