import { useEffect, useState } from "preact/hooks";

import type { OptionField, OwnerCreditBalances, OwnerDashboard, OwnerStatement } from "../api/types";
import { formatDateTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { usePos } from "../state";
import { explain } from "./SetupScreen";
import { Icon } from "../ui/Icon";
import { Banner, EmptyState, Loading, PageHeader, Section, Segmented, Stat } from "../ui/Page";

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
    <section class="p-4 lg:p-8 max-w-5xl mx-auto flex flex-col gap-6 rise">
      <PageHeader title="Dueño" subtitle={setup.store.company.name} icon="crown" back={onClose} />
      {!online && <EmptyState icon="cloudOff" title="El módulo del dueño necesita internet" hint="Vuelve a intentarlo cuando regrese la conexión." />}
      {error && <Banner tone="error">{error}</Banner>}
      {sections.length > 0 && (
        <Segmented label="Secciones" value={tab} onChange={setTab}
          options={sections.map((s) => ({ value: s, label: SECTION_LABELS[s] ?? s }))} />
      )}
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
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return <Loading />;
  const peak = Math.max(1, ...data.sales_trend.map((t) => t.amount));
  const pct = data.summary.comparison_vs_yesterday_pct;
  return (
    <div class="flex flex-col gap-6">
      <div class="flex items-center gap-2">
        <span class="label-2 flex-1">{data.date_label}</span>
        <button class="btn btn-ghost btn-sm text-primary" onClick={reload}><Icon name="refresh" size={16} /> Actualizar</button>
      </div>
      <div class="grid gap-4 grid-cols-2 lg:grid-cols-4">
        <Stat label="Ventas de hoy" value={money(data.summary.sales_today)}
          note={pct !== null && (
            <span class={`inline-flex items-center gap-1 font-medium ${pct >= 0 ? "text-success" : "text-danger"}`}>
              <Icon name={pct >= 0 ? "arrowUp" : "arrowDown"} size={14} /> {Math.abs(pct)}% vs. ayer a esta hora
            </span>
          )} />
        <Stat label="Tickets" value={data.summary.tickets} />
        <Stat label="Ticket promedio" value={money(data.summary.average_ticket)} />
        <Stat label="Piezas" value={data.summary.units_sold} />
      </div>
      <div class="surface p-4 flex flex-col gap-4">
        <h2 class="font-semibold">Ventas por hora</h2>
        <div class="flex items-end gap-1 h-40" role="img" aria-label="Ventas por hora">
          {data.sales_trend.map((t) => (
            <div key={t.label} class="flex-1 h-full flex flex-col items-center justify-end gap-1" title={`${t.label}:00 · ${money(t.amount)}`}>
              <div class="w-full max-w-8 rounded-t-[8px] rounded-b-[2px] min-h-[2px]"
                style={{ height: `${Math.round((t.amount / peak) * 100)}%`, background: "linear-gradient(180deg, #5ac8fa, #007aff)" }} />
              <span class="text-[10px] label-2 num">{t.label}</span>
            </div>
          ))}
        </div>
      </div>
      <div class="grid gap-6 md:grid-cols-2">
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
    <Section title={title}>
      {rows.length ? rows.map(([left, right], i) => (
        <div key={i} class="row !min-h-12 text-sm">
          <span class="flex-1 min-w-0 truncate">{left}</span>
          <span class="whitespace-nowrap label-2 num">{right}</span>
        </div>
      )) : <div class="row !min-h-12 text-sm label-2">{empty}</div>}
    </Section>
  );
}

function Credit() {
  const { client, setup } = usePos();
  const money = (n: number) => formatMoney(n, setup.store.currency);
  const { data, error } = useLoad<OwnerCreditBalances>(() => client.ownerCredit(setup.register.id));
  const [statement, setStatement] = useState<OwnerStatement | null>(null);
  const [statementError, setStatementError] = useState<string | null>(null);
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return <Loading />;

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
      <div class="flex flex-col gap-6 rise">
        <PageHeader title={statement.customer.name} subtitle={<>Debe <span class="text-danger font-semibold num">{money(statement.balance)}</span></>}
          back={() => setStatement(null)} backLabel="Créditos" />
        <Section title="Estado de cuenta">
          {[...statement.moves].reverse().map((m, i) => (
            <div key={i} class="row !items-start !py-3">
              <div class="flex-1 min-w-0">
                <div class="font-semibold">{m.kind}{m.reference && <span class="label-2 text-xs font-normal"> · {m.reference}</span>}</div>
                <div class="text-xs label-2">{formatDateTime(m.date.replace(" ", "T") + "Z")}</div>
                {m.items.map((it, j) => <div key={j} class="text-xs label-2 num">{it.qty} × {it.name} · {money(it.total)}</div>)}
              </div>
              <div class="text-right text-sm num">
                {m.charge > 0 && <div class="font-semibold">+{money(m.charge)}</div>}
                {m.payment > 0 && <div class="font-semibold text-success">−{money(m.payment)}</div>}
                <div class="label-2 text-xs">saldo {money(m.balance)}</div>
              </div>
            </div>
          ))}
        </Section>
      </div>
    );
  }

  return (
    <div class="flex flex-col gap-6">
      <div class="grid gap-4 grid-cols-2">
        <Stat label="Total por cobrar" value={money(data.total_owed)} tone={data.total_owed > 0 ? "bad" : undefined} />
        <Stat label="Clientes que deben" value={data.customers.length} />
      </div>
      {statementError && <Banner tone="error">{statementError}</Banner>}
      {data.customers.length ? (
        <Section title="Quién debe">
          {data.customers.map((c) => (
            <button key={c.id} class="row !py-3" onClick={() => void open(c.id)}>
              <span class="avatar-disc w-10 h-10 text-base shrink-0" data-role="manager">{(c.name.trim()[0] ?? "?").toUpperCase()}</span>
              <div class="flex-1 min-w-0">
                <div class="font-semibold truncate flex items-center gap-2">
                  {c.name}{c.over_limit && <span class="pill pill-plain !text-[var(--color-error)]">Rebasa su límite</span>}
                </div>
                <div class="text-xs label-2">{c.phone}{c.limit ? ` · límite ${money(c.limit)}` : ""}</div>
              </div>
              <span class="font-semibold num">{money(c.balance)}</span>
              <Icon name="chevron" size={18} class="label-2" />
            </button>
          ))}
        </Section>
      ) : <EmptyState icon="checkCircle" title="Nadie debe" />}
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
  // What the server answered to the last save: the form as it is now (the loaded copy is older).
  const [saved, setSaved] = useState<OptionField[] | null>(null);
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return <Loading />;
  const items = saved ?? data.items;
  const value = (field: OptionField) => (field.name in values ? values[field.name]! : field.value);

  async function save() {
    if (!Object.keys(values).length) return;
    setBusy(true);
    setSaveError(null);
    setMessage(null);
    try {
      const answer = await client.saveOwnerOptions(setup.register.id, values);
      setSaved(answer.items);
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
    <div class="flex flex-col gap-6 max-w-2xl">
      {items.map((field) => (
        <Section key={field.name} footer={field.help}>
          {field.type === "boolean" ? (
            <label class="row cursor-pointer">
              <span class="flex-1 font-medium">{field.label}</span>
              <input type="checkbox" class="toggle" checked={!!value(field)}
                onChange={(e) => setValues({ ...values, [field.name]: e.currentTarget.checked })} />
            </label>
          ) : field.type === "selection" && field.choices!.length > 6 ? (
            // A long list (e.g. the closing time) reads better as a menu.
            <label class="row">
              <span class="flex-1 font-medium">{field.label}</span>
              <select class="select w-auto" value={String(value(field))}
                onChange={(e) => setValues({ ...values, [field.name]: e.currentTarget.value })}>
                {field.choices!.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select>
            </label>
          ) : field.type === "selection" ? (
            <>
              <div class="row !min-h-11 font-medium">{field.label}</div>
              {field.choices!.map((choice) => (
                <label key={choice.value} class="row cursor-pointer">
                  <input type="radio" class="sr-only" name={field.name} checked={value(field) === choice.value}
                    onChange={() => setValues({ ...values, [field.name]: choice.value })} />
                  <span class="flex-1">{choice.label}</span>
                  {value(field) === choice.value && <Icon name="check" size={20} class="text-primary" />}
                </label>
              ))}
            </>
          ) : (
            <div class="row !items-stretch flex-col !gap-2 !py-3">
              <span class="font-medium">{field.label}</span>
              <textarea class="textarea w-full" rows={3} value={String(value(field) ?? "")}
                onInput={(e) => setValues({ ...values, [field.name]: e.currentTarget.value })} />
            </div>
          )}
        </Section>
      ))}
      {saveError && <Banner tone="error">{saveError}</Banner>}
      {message && <Banner tone="ok">{message}</Banner>}
      <button class="btn btn-primary btn-xl" disabled={busy || !Object.keys(values).length} onClick={() => void save()}>
        {busy ? <span class="loading loading-spinner" /> : "Guardar opciones"}
      </button>
    </div>
  );
}

const PURPOSES: Record<string, string> = {
  cash: "Entradas y salidas", staff: "Empleados", owner: "Dueño", catalog: "Productos", closing: "Corte de caja", refund: "Devoluciones",
};

function Authorizations() {
  const { client, setup } = usePos();
  const { data, error } = useLoad(() => client.ownerAuthorizations(setup.register.id));
  if (error) return <Banner tone="error">{error}</Banner>;
  if (!data) return <Loading />;
  if (!data.items.length) return <EmptyState icon="lock" title="Nadie ha autorizado nada todavía" />;
  return (
    <Section title="Registro de autorizaciones" class="max-w-2xl">
      {data.items.map((row, i) => (
        <div key={i} class="row !py-3">
          <span class="avatar-disc w-9 h-9 shrink-0" data-role="manager" aria-hidden="true"><Icon name="lock" size={18} /></span>
          <div class="flex-1 min-w-0">
            <div><strong>{row.authorized_by}</strong> autorizó {PURPOSES[row.purpose] ?? row.purpose}{row.requested_by ? <> a <strong>{row.requested_by}</strong></> : null}</div>
            <div class="text-xs label-2">{formatDateTime(row.date)}</div>
          </div>
        </div>
      ))}
    </Section>
  );
}
