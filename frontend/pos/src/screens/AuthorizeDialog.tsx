import { useEffect, useRef, useState } from "preact/hooks";

import type { Employee } from "../api/types";
import { explain } from "./SetupScreen";
import { pinKey } from "./LoginScreen";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];

/**
 * "Ask someone allowed": the module is locked for the person at the register,
 * so someone who may use it enters their PIN (on screen or keyboard). The
 * server checks it and opens the module briefly; who authorized whom is logged.
 */
export function AuthorizeDialog({ title, authorizers, onAuthorize, onCancel }: {
  title: string;
  authorizers: Employee[];
  onAuthorize: (employee: Employee, pin: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [chosen, setChosen] = useState<Employee | null>(authorizers.length === 1 ? authorizers[0]! : null);
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(value: string) {
    if (!chosen || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onAuthorize(chosen, value);
    } catch (err) {
      setError(explain(err));
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  function press(key: string) {
    if (busy || !key) return;
    setError(null);
    if (key === "⌫") return setPin((value) => value.slice(0, -1));
    const next = (pin + key).slice(0, 8);
    setPin(next);
    if (next.length >= 4) void submit(next);
  }

  const pressRef = useRef(press);
  pressRef.current = press;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") return onCancel();
      if (!chosen || event.ctrlKey || event.altKey || event.metaKey) return;
      const key = pinKey(event);
      if (!key) return;
      event.preventDefault();
      event.stopPropagation();
      pressRef.current(key);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [chosen]);

  return (
    <dialog class="modal modal-open" aria-label={`Autorizar: ${title}`}>
      <div class="modal-box flex flex-col items-center gap-3 text-center">
        <div class="text-3xl" aria-hidden="true">🔒</div>
        <h3 class="text-lg font-bold">{title}</h3>
        {!authorizers.length ? (
          <p>Nadie en esta caja puede autorizarlo todavía (o no tiene PIN).</p>
        ) : !chosen ? (
          <>
            <p class="text-sm opacity-70">Pide a alguien con permiso que ponga su PIN:</p>
            <div class="flex flex-wrap gap-2 justify-center">
              {authorizers.map((e) => (
                <button key={e.id} class="btn btn-outline" onClick={() => setChosen(e)}>{e.name}</button>
              ))}
            </div>
          </>
        ) : (
          <>
            <p class="text-sm">PIN de <strong>{chosen.name}</strong></p>
            <div class="text-3xl tracking-[0.5em] h-10" aria-label="PIN">{"•".repeat(pin.length)}</div>
            <div class="grid grid-cols-3 gap-2 w-56">
              {KEYS.map((key, index) => key
                ? <button key={index} class="btn" disabled={busy} onClick={() => press(key)}>{key}</button>
                : <span key={index} />)}
            </div>
            {authorizers.length > 1 && <button class="btn btn-ghost btn-xs" onClick={() => { setChosen(null); setPin(""); }}>Otra persona</button>}
          </>
        )}
        {busy && <span class="loading loading-spinner" />}
        {error && <div role="alert" class="alert alert-error">{error}</div>}
        <p class="text-xs opacity-60">Queda registrado quién autorizó. El permiso dura sólo mientras estés en este módulo.</p>
        <div class="modal-action">
          <button class="btn" onClick={onCancel}>Cancelar</button>
        </div>
      </div>
    </dialog>
  );
}
