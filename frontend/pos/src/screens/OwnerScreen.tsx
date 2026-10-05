import { useEffect, useState } from "preact/hooks";

import type { OptionField, OwnerCreditBalances, OwnerDashboard, OwnerStatement } from "../api/types";
import { formatDateTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { usePos } from "../state";
import { explain } from "./SetupScreen";

const SECTION_LABELS: Record<string, string> = {
  dashboard: "Resumen del día",
  credit: "Créditos",
  options: "Opciones de la caja",
  authorizations: "Autorizaciones",
};

/**
 * The owner's module inside the register (owner's PIN only). Sections come
 * from the server, depending on what the store has installed; the figures
 * are VLUX Owner's own, computed by the same server code.
 */
export function OwnerScreen({ onClose, onOptionsSaved }: { onClose: () => void; onOptionsSaved: () => Promise<void> }) {
  const { client, setup, online } = usePos();
  const [sections, setSections] = useState<string[]>([]);
  const [tab, setTab] = useState<string>("dashboard");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!online) return;
    client.ownerSections(setup.register.id)
      .then(({ items }) => {
        // Known sections in a fixed order; sections from other modules after them.
        const known = Object.keys(SECTION_LABELS);
        const ordered = [...known.filter((s) => items.includes(s)), ...items.filter((s) => !known.includes(s))];
        setSections(ordered);
        setTab((t) => (ordered.includes(t) ? t : ordered[0] ?? "options"));
      })
      .catch((err) => setError(explain(err)));
  }, [online]);

  return (
    <section class="p-4 max-w-5xl mx-auto flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <h2 class="text-xl flex-1">👑 Dueño</h2>
        <button class="btn btn-ghost btn-sm" onClick={onClose}>Volver a vender</button>
      </div>
      {!online && <div role="alert" class="alert alert-warning">El módulo del dueño necesita internet.</div>}
      {error && <div role="alert" class="alert alert-error">{error}</div>}
      <div role="tablist" class="tabs tabs-box">
        {sections.map((s) => (
          <button key={s} role="tab" class={`tab ${tab === s ? "tab-active" : ""}`} onClick={() => setTab(s)}>
            {SECTION_LABELS[s] ?? s}
          </button>
        ))}
      </div>
      {tab === "dashboard" && sections.includes("dashboard") && <Dashboard />}
      {tab === "credit" && sections.includes("credit") && <Credit />}
      {tab === "options" && sections.includes("options") && <Options onSaved={onOptionsSaved} />}
      {tab === "authorizations" && sections.includes("authorizations") && <Authorizations />}
    </section>
  );
}

function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let current = true;
    setError(null);
    load().then((value) => current && setData(value)).catch((err) => current && setError(explain(err)));
    return () => { current = false; };
  }, [round, ...deps]);
  return { data, error, reload: () => setRound((r) => r + 1) };
}

function Dashboard() {
  const { client, setup } = usePos();
  const money = (n: number) => formatMoney(n, setup.store.currency);
  const { data, error, reload } = useLoad<OwnerDashboard>(() => client.ownerDashboard(setup.register.id));
  if (error) return <div role="alert" class="alert alert-error">{error}</div>;
  if (!data) return <span class="loading loading-spinner" />;
  const peak = Math.max(1, ...data.sales_trend.map((t) => t.amount));
  const pct = data.summary.comparison_vs_yesterday_pct;
  return (
    <div class="flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <span class="opacity-70 flex-1">{data.date_label}</span>
        <button class="btn btn-ghost btn-xs" onClick={reload}>Actualizar</button>
      </div>
      <div class="stats stats-vertical sm:stats-horizontal bg-base-100 shadow">
        <div class="stat">
          <div class="stat-title">Ventas de hoy</div>
          <div class="stat-value text-2xl">{money(data.summary.sales_today)}</div>
          {pct !== null && <div class={`stat-desc ${pct >= 0 ? "text-success" : "text-error"}`}>{pct >= 0 ? "▲" : "▼"} {Math.abs(pct)}% vs. ayer a esta hora</div>}
        </div>
        <div class="stat"><div class="stat-title">Tickets</div><div class="stat-value text-2xl">{data.summary.tickets}</div></div>
        <div class="stat"><div class="stat-title">Ticket promedio</div><div class="stat-value text-2xl">{money(data.summary.average_ticket)}</div></div>
        <div class="stat"><div class="stat-title">Piezas</div><div class="stat-value text-2xl">{data.summary.units_sold}</div></div>
      </div>
      <div class="card bg-base-100 shadow"><div class="card-body p-4">
        <h3 class="font-semibold">Ventas por hora</h3>
        <div class="flex items-end gap-1 h-32" aria-label="Ventas por hora">
          {data.sales_trend.map((t) => (
            <div key={t.label} class="flex-1 flex flex-col items-center gap-1" title={`${t.label}:00 · ${money(t.amount)}`}>
              <div class="w-full bg-primary rounded-t" style={{ height: `${Math.round((t.amount / peak) * 100)}%` }} />
              <span class="text-[10px] opacity-60">{t.label}</span>
            </div>
          ))}
        </div>
      </div></div>
      <div class="grid gap-3 md:grid-cols-2">
        <List title="Por caja" rows={data.sales_by_register.map((r) => [`${r.name}${r.state === "open" ? " (abierta)" : ""}`, `${money(r.amount)} · ${r.share_pct}%`])} />
        <List title="Más vendidos" rows={data.top_products.map((p) => [p.name, `${p.qty} · ${money(p.amount)}`])} />
        <List title="Últimas ventas" rows={data.latest_sales.map((s) => [`${s.time} · ${s.reference}`, `${money(s.amount)} · ${s.cashier}`])} />
        <List title="Stock bajo" rows={data.low_stock.map((p) => [p.name, `${p.qty_available} (mín. ${p.threshold})`])} empty="Nada con stock bajo." />
      </div>
    </div>
  );
}

function List({ title, rows, empty = "Sin datos todavía." }: { title: string; rows: [string, string][]; empty?: string }) {
  return (
    <div class="card bg-base-100 shadow"><div class="card-body p-4 gap-1">
      <h3 class="font-semibold">{title}</h3>
      {rows.length ? rows.map(([left, right], i) => (
        <div key={i} class="flex justify-between gap-2 text-sm border-b border-base-200 py-1">
          <span class="truncate">{left}</span><span class="whitespace-nowrap opacity-80">{right}</span>
        </div>
      )) : <p class="text-sm opacity-60">{empty}</p>}
    </div></div>
  );
}

function Credit() {
  const { client, setup } = usePos();
  const money = (n: number) => formatMoney(n, setup.store.currency);
  const { data, error } = useLoad<OwnerCreditBalances>(() => client.ownerCredit(setup.register.id));
  const [statement, setStatement] = useState<OwnerStatement | null>(null);
  const [statementError, setStatementError] = useState<string | null>(null);
  if (error) return <div role="alert" class="alert alert-error">{error}</div>;
  if (!data) return <span class="loading loading-spinner" />;

  async function open(partnerId: number) {
    setStatementError(null);
    try {
      setStatement(await client.ownerStatement(setup.register.id, partnerId));
    } catch (err) {
      setStatementError(explain(err));
    }
  }

  if (statement) {
    return (
      <div class="flex flex-col gap-2">
        <div class="flex items-center gap-2">
          <h3 class="text-lg flex-1">{statement.customer.name} · debe {money(statement.balance)}</h3>
          <button class="btn btn-ghost btn-sm" onClick={() => setStatement(null)}>Volver</button>
        </div>
        <ul class="list bg-base-100 rounded-box">
          {[...statement.moves].reverse().map((m, i) => (
            <li key={i} class="list-row">
              <div class="list-col-grow">
                <div class="font-semibold">{m.kind}{m.reference && <span class="opacity-60 text-xs"> · {m.reference}</span>}</div>
                <div class="text-xs opacity-70">{formatDateTime(m.date.replace(" ", "T") + "Z")}</div>
                {m.items.map((it, j) => <div key={j} class="text-xs">{it.qty} × {it.name} · {money(it.total)}</div>)}
              </div>
              <div class="text-right text-sm">
                {m.charge > 0 && <div>+{money(m.charge)}</div>}
                {m.payment > 0 && <div class="text-success">−{money(m.payment)}</div>}
                <div class="opacity-60 text-xs">saldo {money(m.balance)}</div>
              </div>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div class="flex flex-col gap-3">
      <div class="stats bg-base-100 shadow"><div class="stat">
        <div class="stat-title">Total por cobrar</div><div class="stat-value text-2xl">{money(data.total_owed)}</div>
      </div></div>
      {statementError && <div role="alert" class="alert alert-error">{statementError}</div>}
      <ul class="list bg-base-100 rounded-box">
        {data.customers.map((c) => (
          <li key={c.id} class="list-row items-center">
            <div class="list-col-grow">
              <div class="font-semibold">{c.name}{c.over_limit && <span class="badge badge-error badge-sm ml-2">Rebasa su límite</span>}</div>
              <div class="text-xs opacity-70">{c.phone}{c.limit ? ` · límite ${money(c.limit)}` : ""}</div>
            </div>
            <div class="font-semibold">{money(c.balance)}</div>
            <button class="btn btn-sm" onClick={() => void open(c.id)}>Estado de cuenta</button>
          </li>
        ))}
      </ul>
      {!data.customers.length && <p class="opacity-60">Nadie debe.</p>}
      {data.flagged.length > 0 && (
        <List title="Ventas a crédito para revisar" rows={data.flagged.map((f) => [`${f.reference} · ${f.customer}`, `${money(f.amount)} · ${f.issues.join("; ")}`])} />
      )}
    </div>
  );
}

function Options({ onSaved }: { onSaved: () => Promise<void> }) {
  const { client, setup } = usePos();
  const { data, error } = useLoad<{ items: OptionField[] }>(() => client.ownerOptions(setup.register.id));
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (error) return <div role="alert" class="alert alert-error">{error}</div>;
  if (!data) return <span class="loading loading-spinner" />;
  const value = (field: OptionField) => (field.name in values ? values[field.name]! : field.value);

  async function save() {
    if (!Object.keys(values).length) return;
    setBusy(true);
    setSaveError(null);
    setMessage(null);
    try {
      await client.saveOwnerOptions(setup.register.id, values);
      setValues({});
      setMessage("Opciones guardadas. La caja ya las usa.");
      await onSaved();
    } catch (err) {
      setSaveError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div class="flex flex-col gap-3 max-w-2xl">
      {data.items.map((field) => (
        <fieldset key={field.name} class="fieldset bg-base-100 rounded-box p-3">
          <legend class="fieldset-legend">{field.label}</legend>
          {field.type === "boolean" ? (
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" class="toggle" checked={!!value(field)}
                onChange={(e) => setValues({ ...values, [field.name]: e.currentTarget.checked })} />
              <span>{value(field) ? "Sí" : "No"}</span>
            </label>
          ) : field.type === "selection" ? (
            <div class="flex flex-col gap-1">
              {field.choices!.map((choice) => (
                <label key={choice.value} class="flex items-center gap-2 cursor-pointer">
                  <input type="radio" class="radio radio-sm" name={field.name} checked={value(field) === choice.value}
                    onChange={() => setValues({ ...values, [field.name]: choice.value })} />
                  {choice.label}
                </label>
              ))}
            </div>
          ) : (
            <textarea class="textarea w-full" rows={3} value={String(value(field) ?? "")}
              onInput={(e) => setValues({ ...values, [field.name]: e.currentTarget.value })} />
          )}
          {field.help && <p class="text-xs opacity-60">{field.help}</p>}
        </fieldset>
      ))}
      {saveError && <div role="alert" class="alert alert-error">{saveError}</div>}
      {message && <div role="status" class="alert alert-success">{message}</div>}
      <button class="btn btn-primary" disabled={busy || !Object.keys(values).length} onClick={() => void save()}>
        {busy ? <span class="loading loading-spinner" /> : "Guardar opciones"}
      </button>
    </div>
  );
}

const PURPOSES: Record<string, string> = {
  cash: "Entradas y salidas", staff: "Empleados", owner: "Dueño", catalog: "Productos", closing: "Corte de caja",
};

function Authorizations() {
  const { client, setup } = usePos();
  const { data, error } = useLoad(() => client.ownerAuthorizations(setup.register.id));
  if (error) return <div role="alert" class="alert alert-error">{error}</div>;
  if (!data) return <span class="loading loading-spinner" />;
  return (
    <ul class="list bg-base-100 rounded-box max-w-2xl">
      {data.items.map((row, i) => (
        <li key={i} class="list-row">
          <div class="list-col-grow">
            <div><strong>{row.authorized_by}</strong> autorizó {PURPOSES[row.purpose] ?? row.purpose}{row.requested_by ? <> a <strong>{row.requested_by}</strong></> : null}</div>
            <div class="text-xs opacity-70">{formatDateTime(row.date)}</div>
          </div>
        </li>
      ))}
      {!data.items.length && <li class="list-row opacity-60">Nadie ha autorizado nada todavía.</li>}
    </ul>
  );
}
