import { useEffect, useState } from "preact/hooks";

import type { Employee } from "../api/types";
import { getMeta, setMeta } from "../db/db";
import { type GuardState, lockedFor, OPEN, recordFailure, recordSuccess } from "../lib/guard";
import { pinMatches } from "../lib/pin";
import { usePos } from "../state";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];
const META_GUARD = "pin_guard";

/**
 * Who is at the register. Works without the network: PINs are checked
 * locally. Repeated wrong PINs lock the keypad for a while (lib/guard).
 */
export function LoginScreen() {
  const { db, employees, setEmployee } = usePos();
  const [chosen, setChosen] = useState<Employee | null>(null);
  const [pin, setPin] = useState("");
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

  async function submit(value: string) {
    if (!chosen || lockedFor(guard, Date.now())) return;
    setPin("");
    if (await pinMatches(chosen, value)) {
      await saveGuard(recordSuccess());
      setEmployee(chosen);
      return;
    }
    const next = recordFailure(guard, Date.now());
    await saveGuard(next);
    setNow(Date.now());
    setError(lockedFor(next, Date.now()) ? null : "PIN incorrecto.");
  }

  function press(key: string) {
    if (waitMs) return;
    setError(null);
    if (key === "⌫") return setPin((value) => value.slice(0, -1));
    if (!key) return;
    const next = (pin + key).slice(0, 8);
    setPin(next);
    if (next.length >= 4) void submit(next);
  }

  if (!employees.length) {
    return (
      <div role="alert" class="alert alert-warning m-4">
        No hay empleados para esta caja todavía. Conéctate a internet para descargarlos.
      </div>
    );
  }

  if (!chosen) {
    return (
      <section class="p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {employees.map((employee) => (
          <button key={employee.id} class="btn btn-lg h-auto py-6 flex-col" onClick={() => employee.pin_sha1 ? setChosen(employee) : setEmployee(employee)}>
            <span class="text-lg">{employee.name}</span>
            <span class="badge badge-ghost">{employee.role === "manager" ? "Encargado" : "Cajero"}</span>
          </button>
        ))}
      </section>
    );
  }

  return (
    <section class="p-4 flex flex-col items-center gap-4">
      <h2 class="text-xl">{chosen.name}</h2>
      <div class="text-3xl tracking-[0.5em] h-10" aria-label="PIN">{"•".repeat(pin.length)}</div>
      {waitMs > 0 && (
        <div role="alert" class="alert alert-warning">
          Demasiados intentos. Espera {Math.ceil(waitMs / 1000)} s.
        </div>
      )}
      {error && <div role="alert" class="alert alert-error">{error}</div>}
      <div class="grid grid-cols-3 gap-2 w-64">
        {KEYS.map((key, index) =>
          key ? (
            <button key={index} class="btn btn-lg" disabled={waitMs > 0} onClick={() => press(key)}>{key}</button>
          ) : (
            <span key={index} />
          ),
        )}
      </div>
      <button class="btn btn-ghost" onClick={() => { setChosen(null); setPin(""); }}>Cambiar de empleado</button>
    </section>
  );
}
