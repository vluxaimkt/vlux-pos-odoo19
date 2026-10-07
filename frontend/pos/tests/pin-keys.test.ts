import { describe, expect, it } from "vitest";

import { pinKey } from "../src/screens/LoginScreen";
import { BACKSPACE, typePin } from "../src/ui/Pin";

describe("PIN from the keyboard", () => {
  it("takes the number row, the numeric keypad (Num Lock on or off) and erase keys", () => {
    expect(pinKey({ key: "7", code: "Digit7" })).toBe("7");
    expect(pinKey({ key: "7", code: "Numpad7" })).toBe("7");
    expect(pinKey({ key: "Home", code: "Numpad7" })).toBe("7");
    expect(pinKey({ key: "Backspace", code: "Backspace" })).toBe("Backspace");
    expect(pinKey({ key: "a", code: "KeyA" })).toBeNull();
    expect(pinKey({ key: "Enter", code: "NumpadEnter" })).toBeNull();
  });
});

describe("typing a PIN", () => {
  it("builds each key on the digits before it, so fast typing loses nothing", () => {
    // Four keys applied one after another, as they arrive before any render.
    const typed = ["1", "2", "3", "4"].reduce((pin, key) => typePin(pin, key), "");
    expect(typed).toBe("1234");
  });

  it("erases with Backspace, ignores other keys and stops at the maximum", () => {
    expect(typePin("123", BACKSPACE)).toBe("12");
    expect(typePin("", BACKSPACE)).toBe("");
    expect(typePin("12", "a")).toBe("12");
    expect(typePin("12", "")).toBe("12");
    expect(typePin("12345678", "9")).toBe("12345678");
    expect(typePin("123", "4", 4)).toBe("1234");
    expect(typePin("1234", "5", 4)).toBe("1234");
  });
});
