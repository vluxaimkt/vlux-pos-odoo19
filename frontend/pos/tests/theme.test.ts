import { describe, expect, it } from "vitest";

import { readTheme, resolvesDark } from "../src/lib/theme";

const store = (value: string | null) => ({ getItem: () => value });

describe("theme of this device", () => {
  it("reads light or dark, and anything else as the system's", () => {
    expect(readTheme(store("light"))).toBe("light");
    expect(readTheme(store("dark"))).toBe("dark");
    expect(readTheme(store(null))).toBe("system");
    expect(readTheme(store("purple"))).toBe("system");
    expect(readTheme(null)).toBe("system");
    expect(readTheme({ getItem: () => { throw new Error("blocked"); } })).toBe("system");
  });

  it("follows the system only when the choice is the system's", () => {
    expect(resolvesDark("system", true)).toBe(true);
    expect(resolvesDark("system", false)).toBe(false);
    expect(resolvesDark("dark", false)).toBe(true);
    expect(resolvesDark("light", true)).toBe(false);
  });
});
