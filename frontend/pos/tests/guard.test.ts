import { describe, expect, it } from "vitest";

import { lockedFor, OPEN, recordFailure, recordSuccess } from "../src/lib/guard";

const policy = { maxFailures: 3, baseLockMs: 1000, maxLockMs: 3000 };

describe("PIN guard", () => {
  it("locks after repeated wrong PINs, longer each time, capped", () => {
    let state = OPEN;
    state = recordFailure(state, 0, policy);
    state = recordFailure(state, 0, policy);
    expect(lockedFor(state, 0)).toBe(0);
    state = recordFailure(state, 0, policy);
    expect(lockedFor(state, 0)).toBe(1000);
    expect(lockedFor(state, 1000)).toBe(0);

    for (let i = 0; i < 3; i++) state = recordFailure(state, 1000, policy);
    expect(lockedFor(state, 1000)).toBe(2000);
    for (let i = 0; i < 3; i++) state = recordFailure(state, 5000, policy);
    expect(lockedFor(state, 5000)).toBe(3000);
  });

  it("a right PIN clears it", () => {
    const locked = recordFailure(recordFailure(recordFailure(OPEN, 0, policy), 0, policy), 0, policy);
    expect(recordSuccess()).toEqual(OPEN);
    expect(lockedFor(locked, 0)).toBeGreaterThan(0);
  });
});
