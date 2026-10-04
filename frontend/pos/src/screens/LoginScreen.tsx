import { useEffect, useState } from "preact/hooks";

import { ApiError, isRetryable } from "../api/client";
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
  const { db, client, setup, online, employees, setEmployee } = usePos();
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
      <section class="p-4 flex flex-col gap-3">
        <h2 class="text-xl">¿Quién está en la caja?</h2>
        {error && <div role="alert" class="alert alert-warning">{error}</div>}
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {employees.map((employee) => (
            <button key={employee.id}
              class="btn btn-lg btn-outline h-auto py-6 flex-col gap-2 border-2 bg-base-100 hover:btn-primary"
              onClick={() => pick(employee)}>
              <span class="text-lg">{employee.name}</span>
              <span class={`badge ${employee.role === "manager" ? "badge-primary" : "badge-neutral"}`}>
                {employee.role === "manager" ? "Encargado" : "Cajero"}
              </span>
            </button>
          ))}
        </div>
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
