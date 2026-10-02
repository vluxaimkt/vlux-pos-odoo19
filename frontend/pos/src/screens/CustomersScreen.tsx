import { useEffect, useState } from "preact/hooks";

import type { AbonoTicket, CreditRow, Customer } from "../api/types";
import { customerRow } from "../db/db";
import { formatMoney } from "../lib/money";
import { normalize } from "../lib/text";
import type { CartCustomer } from "../sale/cart";
import { usePos } from "../state";
import { explain } from "./SetupScreen";

type Mode =
  | { name: "list" }
  | { name: "new" }
  | { name: "credit"; customer: CartCustomer }
  | { name: "abono"; customer: CartCustomer }
  | { name: "abono-ticket"; ticket: AbonoTicket };

/**
 * Customers and their credit (fiado), as on the Odoo POS: who owes what, the
 * limit and what is left; the encargado authorises credit; any cashier
 * receives abonos. With `onPick`, it chooses the customer of a sale.
 */
export function CustomersScreen({ onClose, onPick }: { onClose: () => void; onPick?: (customer: CartCustomer) => void }) {
  const { db, setup, credit, canSellOnCredit, online, refreshCredit } = usePos();
  const [mode, setMode] = useState<Mode>({ name: "list" });
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Customer[]>([]);
  const currency = setup.store.currency;
  const money = (value: number) => formatMoney(value, currency);

  useEffect(() => {
    if (online) void refreshCredit().catch(() => undefined);
  }, [online]);

  useEffect(() => {
    let current = true;
    void (async () => {
      const words = normalize(query.trim()).split(/\s+/).filter(Boolean);
      if (!words.length) return current && setFound([]);
      const digits = query.replace(/\D/g, "");
      const rows = await db.customers
        .filter((c) => c.active && (words.every((w) => c.search.includes(w)) || (digits.length >= 4 && (c.phone ?? "").replace(/\D/g, "").includes(digits))))
        .limit(50)
        .toArray();
      if (current) setFound(rows.sort((a, b) => a.name.localeCompare(b.name)));
    })();
    return () => {
      current = false;
    };
  }, [db, query]);

  if (mode.name === "new") {
    return <NewCustomer onCancel={() => setMode({ name: "list" })} onCreated={(customer) => {
      if (onPick) return onPick(customer);
      setQuery(customer.name);
      setMode({ name: "list" });
    }} />;
  }
  if (mode.name === "credit") return <CreditForm customer={mode.customer} onDone={() => setMode({ name: "list" })} />;
  if (mode.name === "abono") {
    return <AbonoForm customer={mode.customer} onCancel={() => setMode({ name: "list" })}
      onDone={(ticket) => setMode({ name: "abono-ticket", ticket })} />;
  }
  if (mode.name === "abono-ticket") return <AbonoReceipt ticket={mode.ticket} onDone={() => setMode({ name: "list" })} />;

  // No search: the customers with credit, who owes most first.
  const rows: { id: number; name: string; phone: string | null }[] = query.trim()
    ? found
    : [...credit.values()].sort((a, b) => b.balance - a.balance).map((row) => ({ id: row.partner_id, name: row.name, phone: row.phone }));
  const owed = [...credit.values()].reduce((total, row) => total + Math.max(0, row.balance), 0);

  return (
    <section class="p-4 max-w-3xl mx-auto flex flex-col gap-3">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="text-xl flex-1">{onPick ? "Elegir cliente" : "Clientes y crédito"}</h2>
        <button class="btn btn-sm" onClick={() => setMode({ name: "new" })} disabled={!online}>Nuevo cliente</button>
        <button class="btn btn-ghost btn-sm" onClick={onClose}>{onPick ? "Cancelar" : "Volver a vender"}</button>
      </div>
      <input class="input input-lg w-full" type="search" autofocus autocomplete="off"
        placeholder="Busca por nombre o teléfono" value={query}
        onInput={(event) => setQuery(event.currentTarget.value)} />
      {!query.trim() && (
        <div class="text-sm opacity-80">
          Clientes con crédito · por cobrar <strong>{money(owed)}</strong>
          {!online && " · sin internet: saldos de la última sincronización"}
        </div>
      )}
      {!rows.length && <p class="opacity-60">{query.trim() ? "Ningún cliente con ese nombre o teléfono." : "Aún no hay clientes con crédito."}</p>}
      <ul class="list bg-base-100 rounded-box">
        {rows.map((customer) => {
          const row = credit.get(customer.id);
          const pick = { id: customer.id, name: customer.name };
          return (
            <li key={customer.id} class="list-row items-center">
              <button class="list-col-grow text-left" disabled={!onPick} onClick={() => onPick?.(pick)}>
                <div class="font-semibold">{customer.name}</div>
                {customer.phone && <div class="text-xs opacity-60">{customer.phone}</div>}
                <CreditLine row={row} money={money} />
              </button>
              {onPick ? (
                <button class="btn btn-sm btn-primary" onClick={() => onPick(pick)}>Elegir</button>
              ) : (
                <div class="flex flex-wrap gap-1 justify-end">
                  {row && row.balance > 0 && (
                    <button class="btn btn-sm btn-primary" disabled={!online} onClick={() => setMode({ name: "abono", customer: pick })}>Abonar</button>
                  )}
                  {canSellOnCredit && (
                    <button class="btn btn-sm" disabled={!online} onClick={() => setMode({ name: "credit", customer: pick })}>Crédito…</button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function CreditLine({ row, money }: { row: CreditRow | undefined; money: (n: number) => string }) {
  if (!row) return <div class="text-xs opacity-60">Sin crédito</div>;
  return (
    <div class="text-sm flex flex-wrap gap-x-3">
      {row.balance > 0 ? <span class="text-error font-semibold">Debe {money(row.balance)}</span> : <span class="opacity-70">No debe</span>}
      {row.allowed ? (
        <span class="opacity-80">
          Límite {row.limit ? money(row.limit) : "sin límite"}
          {row.available !== null && <> · disponible {money(Math.max(0, row.available))}</>}
        </span>
      ) : (
        <span class="badge badge-ghost badge-sm">Crédito no autorizado</span>
      )}
      {row.over_limit && <span class="badge badge-error badge-sm">Rebasa su límite</span>}
    </div>
  );
}

function NewCustomer({ onCancel, onCreated }: { onCancel: () => void; onCreated: (customer: CartCustomer) => void }) {
  const { db, client } = usePos();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(event: Event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await client.createCustomer({
        name: name.trim(), ...(phone.trim() ? { phone: phone.trim() } : {}), ...(email.trim() ? { email: email.trim() } : {}),
      });
      // Into the local copy right away; the next sync brings the rest.
      await db.customers.put(customerRow({
        ...created, barcode: null, active: true, sync_date: new Date().toISOString(),
      } as Customer));
      onCreated({ id: created.id, name: created.name });
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form class="p-4 max-w-md mx-auto flex flex-col gap-2" onSubmit={save}>
      <h2 class="text-xl">Nuevo cliente</h2>
      <fieldset class="fieldset">
        <legend class="fieldset-legend">Nombre</legend>
        <input class="input w-full" required maxLength={128} value={name} onInput={(e) => setName(e.currentTarget.value)} />
      </fieldset>
      <fieldset class="fieldset">
        <legend class="fieldset-legend">Teléfono (opcional)</legend>
        <input class="input w-full" type="tel" maxLength={32} value={phone} onInput={(e) => setPhone(e.currentTarget.value)} />
      </fieldset>
      <fieldset class="fieldset">
        <legend class="fieldset-legend">Correo (opcional)</legend>
        <input class="input w-full" type="email" maxLength={128} value={email} onInput={(e) => setEmail(e.currentTarget.value)} />
      </fieldset>
      {error && <div role="alert" class="alert alert-error">{error}</div>}
      <div class="flex gap-2">
        <button type="button" class="btn btn-ghost flex-1" onClick={onCancel}>Cancelar</button>
        <button class="btn btn-primary flex-[2]" disabled={busy || !name.trim()}>Guardar cliente</button>
      </div>
    </form>
  );
}

/** Authorise or withdraw credit and set the limit: encargado or owner (the server checks). */
function CreditForm({ customer, onDone }: { customer: CartCustomer; onDone: () => void }) {
  const { client, setup, employee, credit, saveCredit } = usePos();
  const current = credit.get(customer.id);
  const [allowed, setAllowed] = useState(current?.allowed ?? true);
  const [limit, setLimit] = useState(String(current?.limit ?? ""));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(event: Event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      saveCredit(await client.setCredit(customer.id, {
        register_id: setup.register.id, allowed, limit: Number(limit || 0),
        ...(employee ? { employee_id: employee.id } : {}),
      }));
      onDone();
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form class="p-4 max-w-md mx-auto flex flex-col gap-3" onSubmit={save}>
      <h2 class="text-xl">Crédito de {customer.name}</h2>
      <label class="label cursor-pointer gap-3">
        <input type="checkbox" class="toggle toggle-primary" checked={allowed} onChange={(e) => setAllowed(e.currentTarget.checked)} />
        <span>Puede comprar a crédito</span>
      </label>
      {allowed && (
        <fieldset class="fieldset">
          <legend class="fieldset-legend">Límite de crédito (0 = sin límite)</legend>
          <input class="input input-lg w-full" type="number" inputMode="decimal" min="0" step="0.01" max="10000000"
            value={limit} onInput={(e) => setLimit(e.currentTarget.value)} />
        </fieldset>
      )}
      {error && <div role="alert" class="alert alert-error">{error}</div>}
      <div class="flex gap-2">
        <button type="button" class="btn btn-ghost flex-1" onClick={onDone}>Cancelar</button>
        <button class="btn btn-primary flex-[2]" disabled={busy}>Guardar</button>
      </div>
    </form>
  );
}

/** A customer pays (part of) what they owe. Needs the network: the balance must be the real one. */
function AbonoForm({ customer, onCancel, onDone }: { customer: CartCustomer; onCancel: () => void; onDone: (ticket: AbonoTicket) => void }) {
  const { client, setup, employee, credit, saveCredit } = usePos();
  const methods = setup.register.payment_methods.filter((method) => method.type !== "pay_later");
  const [methodId, setMethodId] = useState(methods.find((m) => m.is_cash)?.id ?? methods[0]?.id);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // One id per abono: a retry after a dropped connection is the same abono.
  const [uuid] = useState(() => crypto.randomUUID());
  const debt = credit.get(customer.id)?.balance ?? 0;
  const money = (value: number) => formatMoney(value, setup.store.currency);

  async function save(event: Event) {
    event.preventDefault();
    const value = Number(amount.replace(",", "."));
    if (!Number.isFinite(value) || value <= 0) return setError("Escribe cuánto paga el cliente.");
    if (!methodId) return setError("Esta caja no tiene efectivo ni tarjeta.");
    setBusy(true);
    setError(null);
    try {
      const ticket = await client.registerAbono({
        uuid, register_id: setup.register.id, partner_id: customer.id, amount: value, payment_method_id: methodId,
        ...(employee ? { employee_id: employee.id } : {}),
      });
      saveCredit(ticket.customer);
      onDone(ticket);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form class="p-4 max-w-md mx-auto flex flex-col gap-3" onSubmit={save}>
      <h2 class="text-xl">Abono de {customer.name}</h2>
      <div class="stats bg-base-100 shadow">
        <div class="stat"><div class="stat-title">Debe</div><div class="stat-value text-error">{money(debt)}</div></div>
      </div>
      <fieldset class="fieldset">
        <legend class="fieldset-legend">¿Cuánto paga?</legend>
        <div class="join w-full">
          <input class="input input-lg join-item w-full" type="number" inputMode="decimal" min="0" step="0.01"
            value={amount} onInput={(e) => setAmount(e.currentTarget.value)} />
          <button type="button" class="btn btn-lg join-item" onClick={() => setAmount(String(debt))}>Liquidar todo</button>
        </div>
      </fieldset>
      <div class="flex flex-wrap gap-2">
        {methods.map((method) => (
          <button key={method.id} type="button"
            class={`btn ${methodId === method.id ? "btn-primary" : "btn-outline"}`}
            onClick={() => setMethodId(method.id)}>{method.name}</button>
        ))}
      </div>
      {error && <div role="alert" class="alert alert-error">{error}</div>}
      <div class="flex gap-2">
        <button type="button" class="btn btn-ghost flex-1" onClick={onCancel}>Cancelar</button>
        <button class="btn btn-primary btn-lg flex-[2]" disabled={busy}>Registrar abono</button>
      </div>
    </form>
  );
}

function AbonoReceipt({ ticket, onDone }: { ticket: AbonoTicket; onDone: () => void }) {
  const { setup } = usePos();
  const company = setup.store.company;
  const money = (value: number) => formatMoney(value, setup.store.currency);
  return (
    <section class="p-4 flex flex-col items-center gap-4">
      <article class="receipt bg-white text-black font-mono text-sm p-4 w-[80mm] max-w-full shadow print:shadow-none">
        <div class="text-center font-bold text-base">{company.name}</div>
        <div class="text-center font-bold text-base border border-black mt-2 py-1">ABONO A CUENTA</div>
        <div class="mt-2">{new Date(ticket.date.replace(" ", "T") + "Z").toLocaleString("es-MX")}</div>
        <div>Folio: {ticket.reference ?? ticket.uuid.slice(0, 8)}</div>
        <div>Cliente: {ticket.partner_name}</div>
        <div>Atendió: {ticket.cashier}</div>
        <hr class="my-2 border-dashed border-black" />
        <div class="flex justify-between"><span>Saldo anterior</span><span>{money(ticket.previous_balance)}</span></div>
        <div class="flex justify-between"><span>Abono ({ticket.method})</span><span>{money(ticket.amount)}</span></div>
        <div class="flex justify-between font-bold"><span>Saldo nuevo</span><span>{money(ticket.new_balance)}</span></div>
        {ticket.new_balance <= 0 && <div class="text-center font-bold pt-2">CUENTA LIQUIDADA</div>}
        <hr class="my-2 border-dashed border-black" />
        {company.vat && <div class="text-center">RFC: {company.vat}</div>}
        {company.receipt_legend && <div class="text-center text-xs">{company.receipt_legend}</div>}
      </article>
      <div class="flex gap-2 print:hidden">
        <button class="btn btn-lg" onClick={() => window.print()}>Imprimir</button>
        <button class="btn btn-primary btn-lg" onClick={onDone}>Listo</button>
      </div>
    </section>
  );
}
