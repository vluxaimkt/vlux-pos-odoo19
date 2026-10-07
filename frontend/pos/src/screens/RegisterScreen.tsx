import { useState } from "preact/hooks";

import type { RegisterState } from "../api/types";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { Banner, EmptyState, Field } from "../ui/Page";
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
      <section class="p-4 lg:p-8 max-w-md mx-auto flex flex-col gap-6">
        <EmptyState icon="calculator" title={`La caja está en corte (${state.session.name})`}
          hint="Termínalo en Odoo antes de volver a abrirla." />
      </section>
    );
  }

  return (
    <form class="p-4 lg:p-8 min-h-[70vh] max-w-md mx-auto flex flex-col justify-center gap-6 rise" onSubmit={open}>
      <div class="flex flex-col items-center gap-3 text-center">
        <span class="avatar-disc w-20 h-20 pop" data-role="manager" aria-hidden="true"><Icon name="power" size={40} /></span>
        <h1 class="text-3xl">Abrir {state.name}</h1>
        <p class="label-2">Cuenta el efectivo que hay en el cajón para empezar el turno.</p>
      </div>
      <Field label="Efectivo inicial en el cajón">
        <label class="input input-lg w-full !h-16 text-3xl num">
          <span class="label-2">$</span>
          <input
            type="number"
            inputMode="decimal"
            min="0"
            step="0.01"
            placeholder="0.00"
            aria-label="Efectivo inicial en el cajón"
            autofocus
            value={cash}
            onInput={(event) => setCash(event.currentTarget.value)}
            required={state.cash_control}
          />
        </label>
      </Field>
      {!online && <Banner tone="warn">Abrir la caja necesita internet.</Banner>}
      {error && <Banner tone="error">{error}</Banner>}
      <button class="btn btn-primary btn-xl" type="submit" disabled={busy || !online}>
        {busy ? <span class="loading loading-spinner" /> : "Abrir caja"}
      </button>
    </form>
  );
}
