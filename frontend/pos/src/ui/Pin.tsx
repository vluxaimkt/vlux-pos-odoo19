import { useCallback, useRef, useState } from "preact/hooks";

import { Icon } from "./Icon";

/** The key that deletes the last digit (keypad and keyboard). */
export const BACKSPACE = "Backspace";

/** The PIN after one key: a digit is appended (up to `max`), Backspace deletes, anything else is ignored. */
export function typePin(current: string, key: string, max = 8): string {
  if (key === BACKSPACE) return current.slice(0, -1);
  if (!/^[0-9]$/.test(key)) return current;
  return (current + key).slice(0, max);
}

/**
 * The PIN being typed. It also lives in a ref: keys arriving faster than a
 * render (a fast typist, a keyboard wedge) must build on the digits before
 * them, not on the last rendered value, or digits get lost.
 */
export function usePinEntry(max = 8) {
  const current = useRef("");
  const [pin, setState] = useState("");
  const set = useCallback((value: string) => {
    current.current = value;
    setState(value);
  }, []);
  /** Apply one key; returns the new PIN. */
  const type = useCallback((key: string) => {
    const next = typePin(current.current, key, max);
    set(next);
    return next;
  }, [max, set]);
  return { pin, set, type };
}

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", BACKSPACE];

/** Dots that fill as the PIN is typed (four at least, as on the iOS lock screen). */
export function PinDots({ length, shakeKey }: { length: number; shakeKey?: string | null }) {
  const count = Math.max(4, length);
  return (
    <div key={shakeKey ?? "calm"} class={`flex gap-4 h-8 items-center ${shakeKey ? "shake" : ""}`} role="img" aria-label={`PIN: ${length} dígitos`}>
      {Array.from({ length: count }, (_, index) => (
        <span key={index} class="pin-dot" data-filled={index < length} />
      ))}
    </div>
  );
}

/** Round keypad. `size` picks the 64 px login keys or the 56 px dialog keys. */
export function PinPad({ onPress, disabled = false, compact = false }: { onPress: (key: string) => void; disabled?: boolean; compact?: boolean }) {
  return (
    <div class="grid grid-cols-3 gap-4 justify-items-center">
      {KEYS.map((key, index) => {
        if (!key) return <span key={index} />;
        const back = key === BACKSPACE;
        return (
          <button
            key={index}
            type="button"
            class={`pin-key ${back ? "pin-key-quiet" : ""} ${compact ? "!w-14 !h-14 !text-2xl" : ""}`}
            disabled={disabled}
            aria-label={back ? "Borrar" : key}
            onClick={() => onPress(key)}
          >
            {back ? <Icon name="backspace" size={compact ? 24 : 28} /> : key}
          </button>
        );
      })}
    </div>
  );
}
