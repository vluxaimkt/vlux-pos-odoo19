import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { ApiError, isRetryable } from "../api/client";
import type { Employee } from "../api/types";
import { getMeta, setMeta } from "../db/db";
import { type GuardState, lockedFor, OPEN, recordFailure, recordSuccess } from "../lib/guard";
import { pinMatches } from "../lib/pin";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { Banner, EmptyState } from "../ui/Page";
import { BACKSPACE, PinDots, PinPad, usePinEntry } from "../ui/Pin";

const META_GUARD = "pin_guard";

/** The digit a key stands for: top-row numbers and the numeric keypad (with or without Num Lock). */
export function pinKey(event: Pick<KeyboardEvent, "key" | "code">): string | null {
  if (/^[0-9]$/.test(event.key)) return event.key;
  const pad = /^Numpad([0-9])$/.exec(event.code);
  if (pad) return pad[1] ?? null;
  if (event.key === "Backspace" || event.key === "Delete") return BACKSPACE;
  return null;
}

/**
 * Who is at the register. Works without the network: PINs are checked
 * locally. Repeated wrong PINs lock the keypad for a while (lib/guard).
 */
export function LoginScreen() {
  const { db, client, setup, online, employees, setEmployee } = usePos();
  const [chosen, setChosen] = useState<Employee | null>(null);
  const { pin, set: setPin, type: typeKey } = usePinEntry();
  const [error, setError] = useState<string | null>(null);
  const [guard, setGuard] = useState<GuardState>(OPEN);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    void getMeta<GuardState>(db, META_GUARD).then((stored) => stored && setGuard(stored));
  }, [db]);

  const waitMs = lockedFor(guard, now);
  useEffect(() => {
    if (!waitMs) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [waitMs > 0]);

  async function saveGuard(next: GuardState) {
    setGuard(next);
    await setMeta(db, META_GUARD, next);
  }

  /**
   * With internet the server checks the PIN and answers an employee session
   * (needed to open, close and give credit). Without it, the register checks
   * the PIN itself: sales still work and are marked unverified on the server.
   */
  async function submit(value: string) {
    if (!chosen || lockedFor(guard, Date.now())) return;
    setPin("");
    if (online) {
      try {
        const answer = await client.employeeLogin(setup.register.id, chosen.id, value);
        client.employeeSession = answer.session;
        await saveGuard(recordSuccess());
        setEmployee(chosen);
        return;
      } catch (err) {
        if (err instanceof ApiError && err.code === "INVALID_PIN") {
          await failed();
          return;
        }
        if (!isRetryable(err)) {
          // Locked out, a manager without a PIN…: the server's own words.
          setError(err instanceof Error ? err.message : String(err));
          return;
        }
        // The server could not be reached: fall back to the offline check.
      }
    }
    if (await pinMatches(chosen, value)) {
      client.employeeSession = null;
      await saveGuard(recordSuccess());
      setEmployee(chosen);
      return;
    }
    await failed();
  }

  async function failed() {
    const next = recordFailure(guard, Date.now());
    await saveGuard(next);
    setNow(Date.now());
    setError(lockedFor(next, Date.now()) ? null : "PIN incorrecto.");
  }

  // Typing the PIN on the keyboard (number row or numeric keypad) works like tapping it.
  const pressRef = useRef<(key: string) => void>(() => undefined);
  pressRef.current = press;
  useEffect(() => {
    if (!chosen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.altKey || event.metaKey) return;
      if (event.key === "Escape") {
        setChosen(null);
        setPin("");
        return;
      }
      const key = pinKey(event);
      if (!key) return;
      event.preventDefault();
      pressRef.current(key);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chosen]);

  function press(key: string) {
    if (waitMs) return;
    setError(null);
    if (!key) return;
    const next = typeKey(key);
    if (key !== BACKSPACE && next.length >= 4) void submit(next);
  }

  if (!employees.length) {
    return (
      <Stage>
        <EmptyState icon="people" title="No hay empleados para esta caja todavía"
          hint="Conéctate a internet para descargarlos." />
      </Stage>
    );
  }

  /**
   * A manager may close the register above the difference limit: without a
   * PIN anyone could tap their card and do it. Managers must have a PIN.
   */
  function pick(employee: Employee) {
    setError(null);
    if (employee.pin_sha1) return setChosen(employee);
    if (employee.role === "manager") {
      setError(`${employee.name} es encargado y no tiene PIN. Ponle uno en Odoo (Empleados → ${employee.name} → Ajustes → PIN) y recarga esta página.`);
      return;
    }
    setEmployee(employee);
  }

  if (!chosen) {
    return (
      <Stage wide>
        <div class="rise flex flex-col items-center gap-8 w-full">
          <header class="text-center">
            <h1 class="text-3xl">¿Quién está en la caja?</h1>
            <p class="mt-2 text-[var(--label-secondary)]">Elige tu nombre para empezar.</p>
          </header>
          {error && <div class="max-w-xl w-full"><Banner tone="warn">{error}</Banner></div>}
          <div class="flex flex-wrap justify-center gap-4 w-full">
            {employees.map((employee, index) => (
              <button key={employee.id} type="button"
                class="tap glass rounded-[20px] w-40 py-6 px-4 flex flex-col items-center gap-3 hover:scale-[1.02] hover:shadow-lg rise"
                style={{ animationDelay: `${index * 50}ms` }}
                onClick={() => pick(employee)}>
                <span class="avatar-disc w-16 h-16 text-2xl" data-role={employee.role}>{initial(employee.name)}</span>
                <span class="font-semibold leading-tight text-center break-words w-full">{employee.name}</span>
                <span class="text-xs text-[var(--label-secondary)]">{employee.role === "manager" ? "Encargado" : "Cajero"}</span>
              </button>
            ))}
          </div>
        </div>
      </Stage>
    );
  }

  return (
    <Stage>
      <div class="rise glass rounded-[20px] w-full max-w-sm p-6 flex flex-col items-center gap-4">
        <span class="avatar-disc w-16 h-16 text-2xl" data-role={chosen.role}>{initial(chosen.name)}</span>
        <div class="text-center">
          <h1 class="text-2xl">{chosen.name}</h1>
          <p class="text-sm text-[var(--label-secondary)]">Escribe tu PIN</p>
        </div>
        <PinDots length={pin.length} shakeKey={error} />
        <div class="min-h-5 text-sm text-center" aria-live="polite">
          {waitMs > 0
            ? <span class="text-warning">Demasiados intentos. Espera {Math.ceil(waitMs / 1000)} s.</span>
            : error && <span class="text-error" role="alert">{error}</span>}
        </div>
        <PinPad disabled={waitMs > 0} onPress={press} />
        <button class="btn btn-ghost text-primary" onClick={() => { setChosen(null); setPin(""); }}>
          <Icon name="back" size={20} /> Cambiar de empleado
        </button>
      </div>
    </Stage>
  );
}

function initial(name: string): string {
  return (name.trim()[0] ?? "?").toUpperCase();
}

/** Full-height stage under the lock screen: a soft wash with the content centered. */
function Stage({ children, wide = false }: { children: ComponentChildren; wide?: boolean }) {
  return (
    <div class="min-h-[calc(100vh-4.5rem)] grid place-items-center p-4 sm:p-8">
      <div class={`w-full flex justify-center ${wide ? "max-w-4xl" : "max-w-sm"}`}>{children}</div>
    </div>
  );
}
