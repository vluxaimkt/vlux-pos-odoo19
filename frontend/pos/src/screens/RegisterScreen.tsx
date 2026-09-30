import { useState } from "preact/hooks";

import type { RegisterState } from "../api/types";
import { usePos } from "../state";
import { explain } from "./SetupScreen";

/** The register is closed: count the opening cash and open it. Needs the network. */
export function RegisterScreen({ state, onOpened }: { state: RegisterState; onOpened: (state: RegisterState) => void }) {
  const { client, setup, employee, online } = usePos();
  const [cash, setCash] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function open(event: Event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onOpened(await client.openSession(setup.register.id, {
        opening_cash: Number(cash || 0),
        ...(employee ? { employee_id: employee.id } : {}),
      }));
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  // A session Odoo created but nobody opened ("opening_control") is opened here.
  if (state.session?.state === "closing_control") {
    return (
      <div role="alert" class="alert alert-warning m-4">
        La caja está en corte ({state.session.name}). Termínalo en Odoo antes de volver a abrirla.
      </div>
    );
  }

  return (
    <form class="p-4 max-w-sm mx-auto flex flex-col gap-3" onSubmit={open}>
      <h2 class="text-xl">Abrir {state.name}</h2>
      <label class="form-control">
        <span class="label-text">Efectivo inicial en el cajón</span>
        <input
          class="input input-bordered input-lg"
          type="number"
          inputMode="decimal"
          min="0"
          step="0.01"
          value={cash}
          onInput={(event) => setCash(event.currentTarget.value)}
          required={state.cash_control}
        />
      </label>
      {!online && <div role="alert" class="alert alert-warning">Abrir la caja necesita internet.</div>}
      {error && <div role="alert" class="alert alert-error">{error}</div>}
      <button class="btn btn-primary btn-lg" type="submit" disabled={busy || !online}>
        {busy ? <span class="loading loading-spinner" /> : "Abrir caja"}
      </button>
    </form>
  );
}
