import { Icon } from "./Icon";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];

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
        const back = key === "⌫";
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
