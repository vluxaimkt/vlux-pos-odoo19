/**
 * Light, dark or the system's: a preference of this device (each register or
 * phone keeps its own), as on iOS/macOS. "system" leaves the page to
 * prefers-color-scheme; the other two pin daisyUI's theme and the app's dark
 * tokens with html[data-theme]. Storage can fail (private window, blocked
 * site data): the app then simply follows the system.
 */
export type ThemeChoice = "system" | "light" | "dark";

const KEY = "vlux-pos-theme";
const THEMES = { light: "vlux-light", dark: "vlux-dark" } as const;

/** The stored choice, or "system" when nothing (or nothing valid) is stored. */
export function readTheme(storage: Pick<Storage, "getItem"> | null = safeStorage()): ThemeChoice {
  try {
    const value = storage?.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

/** Whether the page ends up dark for this choice. */
export function resolvesDark(choice: ThemeChoice, systemDark: boolean): boolean {
  return choice === "dark" || (choice === "system" && systemDark);
}

/** Pin (or release) the theme on the page and keep the browser bar's color in step. */
export function applyTheme(choice: ThemeChoice, doc: Document = document): void {
  const root = doc.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", THEMES[choice]);
  const systemDark = typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
  doc.querySelector('meta[name="theme-color"]')?.setAttribute("content", resolvesDark(choice, systemDark) ? "#000000" : "#f2f2f7");
}

/** Remember the choice on this device and apply it. */
export function saveTheme(choice: ThemeChoice, storage: Pick<Storage, "setItem" | "removeItem"> | null = safeStorage()): void {
  try {
    if (choice === "system") storage?.removeItem(KEY);
    else storage?.setItem(KEY, choice);
  } catch {
    // Not remembered (private window…): it still applies now.
  }
  applyTheme(choice);
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}
