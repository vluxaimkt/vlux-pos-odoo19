import { useEffect, useState } from "preact/hooks";

import type { AbonoTicket, CreditRow, Customer } from "../api/types";
import { customerRow } from "../db/db";
import { formatDateTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { normalize } from "../lib/text";
import type { CartCustomer } from "../sale/cart";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { Banner, EmptyState, Field, PageHeader, Stat } from "../ui/Page";
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
  const { db, setup, credit, canAuthorizeCredit, online, refreshCredit } = usePos();
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
  const debtors = [...credit.values()].filter((row) => row.balance > 0).length;

  return (
    <section class="p-4 lg:p-8 max-w-3xl mx-auto flex flex-col gap-6 rise">
      <PageHeader title={onPick ? "Elegir cliente" : "Clientes y crédito"} icon="people"
        back={onClose} backLabel={onPick ? "Venta" : "Vender"}
        actions={
          <button class="btn btn-ghost text-primary" onClick={() => setMode({ name: "new" })} disabled={!online}>
            <Icon name="plus" size={20} /> Nuevo cliente
          </button>
        } />
      <label class="search-field">
        <Icon name="search" size={22} />
        <input type="search" autofocus autocomplete="off" aria-label="Busca por nombre o teléfono"
          placeholder="Busca por nombre o teléfono" value={query}
          onInput={(event) => setQuery(event.currentTarget.value)} />
      </label>
      {!query.trim() && (
        <div class="grid gap-4 grid-cols-2">
          <Stat label="Por cobrar" value={money(owed)} tone={owed > 0 ? "bad" : undefined} />
          <Stat label="Clientes que deben" value={debtors} note={!online ? "Sin internet: saldos de la última sincronización" : undefined} />
        </div>
      )}
      {!rows.length ? (
        query.trim()
          ? <EmptyState icon="search" title="Sin resultados" hint="Ningún cliente con ese nombre o teléfono." />
          : <EmptyState icon="people" title="Aún no hay clientes con crédito" hint="Busca un cliente por nombre o teléfono." />
      ) : (
        <div class="grouped">
          {rows.map((customer) => {
            const row = credit.get(customer.id);
            const pick = { id: customer.id, name: customer.name };
            return (
              <div key={customer.id} class="row !py-3">
                <span class="avatar-disc w-10 h-10 text-base shrink-0" data-role={row?.balance ? "manager" : undefined}>
                  {(customer.name.trim()[0] ?? "?").toUpperCase()}
                </span>
                <button class="flex-1 min-w-0 text-left" disabled={!onPick} onClick={() => onPick?.(pick)}>
                  <div class="font-semibold truncate">{customer.name}</div>
                  {customer.phone && <div class="text-xs label-2">{customer.phone}</div>}
                  <CreditLine row={row} money={money} />
                </button>
                {onPick ? (
                  <button class="btn btn-sm btn-primary" onClick={() => onPick(pick)}>Elegir</button>
                ) : (
                  <div class="flex flex-wrap gap-2 justify-end">
                    {row && row.balance > 0 && (
                      <button class="btn btn-sm btn-primary" disabled={!online} onClick={() => setMode({ name: "abono", customer: pick })}>Abonar</button>
                    )}
                    {canAuthorizeCredit && (
                      <button class="btn btn-sm" disabled={!online} onClick={() => setMode({ name: "credit", customer: pick })}>Crédito</button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function CreditLine({ row, money }: { row: CreditRow | undefined; money: (n: number) => string }) {
  if (!row) return <div class="text-xs label-2">Sin crédito</div>;
  return (
    <div class="text-xs flex flex-wrap items-center gap-x-2 gap-y-1 mt-1">
      {row.balance > 0 ? <span class="text-danger font-semibold num">Debe {money(row.balance)}</span> : <span class="label-2">No debe</span>}
      {row.allowed ? (
        <span class="label-2 num">
          · Límite {row.limit ? money(row.limit) : "sin límite"}
          {row.available !== null && <> · disponible {money(Math.max(0, row.available))}</>}
        </span>
      ) : (
        <span class="pill pill-plain">Crédito no autorizado</span>
      )}
      {row.over_limit && <span class="pill pill-plain !text-[var(--color-error)]">Rebasa su límite</span>}
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
    <form class="p-4 lg:p-8 max-w-md mx-auto flex flex-col gap-6 rise" onSubmit={save}>
      <PageHeader title="Nuevo cliente" icon="person" back={onCancel} backLabel="Clientes" />
      <Field label="Nombre">
        <input class="input w-full" required maxLength={128} autofocus value={name} onInput={(e) => setName(e.currentTarget.value)} />
      </Field>
      <Field label="Teléfono (opcional)">
        <input class="input w-full" type="tel" maxLength={32} value={phone} onInput={(e) => setPhone(e.currentTarget.value)} />
      </Field>
      <Field label="Correo (opcional)">
        <input class="input w-full" type="email" maxLength={128} value={email} onInput={(e) => setEmail(e.currentTarget.value)} />
      </Field>
      {error && <Banner tone="error">{error}</Banner>}
      <button class="btn btn-primary btn-xl" disabled={busy || !name.trim()}>
        {busy ? <span class="loading loading-spinner" /> : "Guardar cliente"}
      </button>
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
    <form class="p-4 lg:p-8 max-w-md mx-auto flex flex-col gap-6 rise" onSubmit={save}>
      <PageHeader title="Crédito" subtitle={customer.name} icon="card" back={onDone} backLabel="Clientes" />
      <div class="grouped">
        <label class="row cursor-pointer">
          <span class="flex-1 font-medium">Puede comprar a crédito</span>
          <input type="checkbox" class="toggle" checked={allowed} onChange={(e) => setAllowed(e.currentTarget.checked)} />
        </label>
      </div>
      {allowed && (
        <Field label="Límite de crédito" hint="0 = sin límite.">
          <input class="input input-lg w-full text-2xl num" type="number" inputMode="decimal" min="0" step="0.01" max="10000000"
            value={limit} onInput={(e) => setLimit(e.currentTarget.value)} />
        </Field>
      )}
      {error && <Banner tone="error">{error}</Banner>}
      <button class="btn btn-primary btn-xl" disabled={busy}>
        {busy ? <span class="loading loading-spinner" /> : "Guardar"}
      </button>
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
    <form class="p-4 lg:p-8 max-w-md mx-auto flex flex-col gap-6 rise" onSubmit={save}>
      <PageHeader title="Abono" subtitle={customer.name} icon="cash" back={onCancel} backLabel="Clientes" />
      <div class="surface p-6 flex flex-col items-center gap-1 text-center">
        <span class="label-2 font-medium">Debe</span>
        <span class="display text-danger">{money(debt)}</span>
      </div>
      <Field label="¿Cuánto paga?">
        <div class="flex gap-2">
          <input class="input input-lg flex-1 text-2xl num" type="number" inputMode="decimal" min="0" step="0.01" autofocus
            value={amount} onInput={(e) => setAmount(e.currentTarget.value)} />
          <button type="button" class="btn btn-lg" onClick={() => setAmount(String(debt))}>Liquidar todo</button>
        </div>
      </Field>
      <div class="segmented" role="radiogroup" aria-label="Forma de pago">
        {methods.map((method) => (
          <button key={method.id} type="button" role="radio" aria-selected={methodId === method.id} aria-checked={methodId === method.id}
            onClick={() => setMethodId(method.id)}>{method.name}</button>
        ))}
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      <button class="btn btn-primary btn-xl" disabled={busy}>
        {busy ? <span class="loading loading-spinner" /> : "Registrar abono"}
      </button>
    </form>
  );
}

function AbonoReceipt({ ticket, onDone }: { ticket: AbonoTicket; onDone: () => void }) {
  const { setup } = usePos();
  const company = setup.store.company;
  const money = (value: number) => formatMoney(value, setup.store.currency);
  const settled = ticket.new_balance <= 0;
  return (
    <section class="p-4 lg:p-8 flex flex-col items-center gap-6">
      <header class="rise flex flex-col items-center gap-2 text-center print:hidden">
        <span class="avatar-disc w-16 h-16 pop" style={{ background: "linear-gradient(180deg, #34c759, #248a3d)" }}>
          <Icon name="check" size={36} />
        </span>
        <h1 class="text-3xl">{settled ? "Cuenta liquidada" : "Abono registrado"}</h1>
        <p class="label-2 num">{ticket.partner_name} · saldo nuevo {money(ticket.new_balance)}</p>
      </header>
      <article class="receipt bg-white text-black font-mono text-sm p-4 w-[80mm] max-w-full rounded-[12px] shadow-lg print:shadow-none print:rounded-none rise" style={{ animationDelay: "100ms" }}>
        <div class="text-center font-bold text-base">{company.name}</div>
        <div class="text-center font-bold text-base border border-black mt-2 py-1">ABONO A CUENTA</div>
        <div class="mt-2">{formatDateTime(ticket.date.replace(" ", "T") + "Z")}</div>
        <div>Folio: {ticket.reference ?? ticket.uuid.slice(0, 8)}</div>
        <div>Cliente: {ticket.partner_name}</div>
        <div>Atendió: {ticket.cashier}</div>
        <hr class="my-2 border-dashed border-black" />
        <div class="flex justify-between"><span>Saldo anterior</span><span>{money(ticket.previous_balance)}</span></div>
        <div class="flex justify-between"><span>Abono ({ticket.method})</span><span>{money(ticket.amount)}</span></div>
        <div class="flex justify-between font-bold"><span>Saldo nuevo</span><span>{money(ticket.new_balance)}</span></div>
        {settled && <div class="text-center font-bold pt-2">CUENTA LIQUIDADA</div>}
        <hr class="my-2 border-dashed border-black" />
        {company.vat && <div class="text-center">RFC: {company.vat}</div>}
        {company.receipt_legend && <div class="text-center text-xs">{company.receipt_legend}</div>}
      </article>
      <div class="flex flex-wrap justify-center gap-4 print:hidden w-full max-w-md">
        <button class="btn btn-xl flex-1 bg-base-100 border-[0.5px] border-[var(--glass-border)]" onClick={() => window.print()}>
          <Icon name="printer" size={20} /> Imprimir
        </button>
        <button class="btn btn-primary btn-xl flex-1" autofocus onClick={onDone}>Listo</button>
      </div>
    </section>
  );
}
