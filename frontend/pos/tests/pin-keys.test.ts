import { describe, expect, it } from "vitest";

import { pinKey } from "../src/screens/LoginScreen";

describe("PIN from the keyboard", () => {
  it("takes the number row, the numeric keypad (Num Lock on or off) and erase keys", () => {
    expect(pinKey({ key: "7", code: "Digit7" })).toBe("7");
    expect(pinKey({ key: "7", code: "Numpad7" })).toBe("7");
    expect(pinKey({ key: "Home", code: "Numpad7" })).toBe("7");
    expect(pinKey({ key: "Backspace", code: "Backspace" })).toBe("⌫");
    expect(pinKey({ key: "a", code: "KeyA" })).toBeNull();
    expect(pinKey({ key: "Enter", code: "NumpadEnter" })).toBeNull();
  });
});
