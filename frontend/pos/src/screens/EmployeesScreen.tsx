import { useEffect, useState } from "preact/hooks";

import type { StaffMember, StaffRole } from "../api/types";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { Banner, Field, Loading, PageHeader, Section } from "../ui/Page";
import { explain } from "./SetupScreen";

export const ROLES: { value: StaffRole; label: string; help: string }[] = [
  { value: "manager", label: "Encargado", help: "Vende, devuelve, hace el corte (aunque no cuadre), mueve efectivo y autoriza crédito." },
  { value: "cashier", label: "Cajero", help: "Vende, devuelve, recibe abonos y hace el corte si cuadra." },
  { value: "minimal", label: "Acceso mínimo", help: "Sólo vende." },
  { value: "none", label: "Sin acceso a esta caja", help: "No puede entrar a esta caja." },
];

const roleLabel = (role: StaffRole) => ROLES.find((r) => r.value === role)?.label ?? role;

type Mode = { name: "list" } | { name: "new" } | { name: "edit"; member: StaffMember };

/**
 * The store's employees and their access to this register: name, role and
 * PIN. Only whoever the register allows (the owner by default); the server
 * checks it. Nobody is deleted: "Dar de baja" archives.
 */
export function EmployeesScreen({ onClose, onChanged }: { onClose: () => void; onChanged: () => Promise<void> }) {
  const { client, setup, online, employee } = usePos();
  const [mode, setMode] = useState<Mode>({ name: "list" });
  const [members, setMembers] = useState<StaffMember[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setMembers((await client.staff(setup.register.id)).items);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (online) void load();
  }, [online]);

  async function saved() {
    setMode({ name: "list" });
    await load();
    // The login list (and its PINs) changes too.
    await onChanged().catch(() => undefined);
  }

  if (mode.name === "new") return <MemberForm onCancel={() => setMode({ name: "list" })} onSaved={saved} />;
  if (mode.name === "edit") {
    return <MemberForm member={mode.member} self={mode.member.id === employee?.id}
      onCancel={() => setMode({ name: "list" })} onSaved={saved} />;
  }

  const order: StaffRole[] = ["manager", "cashier", "minimal", "none"];
  const withAccess = members.filter((m) => m.active && m.role !== "none")
    .sort((a, b) => order.indexOf(a.role) - order.indexOf(b.role) || a.name.localeCompare(b.name));
  const others = members.filter((m) => m.active && m.role === "none");
  const archived = members.filter((m) => !m.active);
  const row = (member: StaffMember) => (
    <button key={member.id} class="row !py-3" disabled={!online} onClick={() => setMode({ name: "edit", member })}>
      <span class="avatar-disc w-10 h-10 text-base shrink-0" data-role={member.role === "manager" ? "manager" : undefined}>
        {(member.name.trim()[0] ?? "?").toUpperCase()}
      </span>
      <div class="flex-1 min-w-0">
        <div class="font-semibold flex items-center gap-2">
          <span class="truncate">{member.name}</span>
          {member.owner && <span class="pill pill-plain"><Icon name="crown" size={12} /> Dueño</span>}
        </div>
        <div class="text-xs label-2">
          {roleLabel(member.role)} · {member.has_pin ? "con PIN" : member.role === "manager"
            ? <span class="pill-warn">sin PIN: un encargado necesita PIN para entrar</span>
            : "sin PIN"}
          {member.user ? ` · usuario ${member.user}` : ""}
        </div>
      </div>
      <Icon name="chevron" size={18} class="label-2" />
    </button>
  );
  return (
    <section class="p-4 lg:p-8 max-w-3xl mx-auto flex flex-col gap-6 rise">
      <PageHeader title="Empleados" subtitle={setup.register.name} icon="idcard" back={onClose}
        actions={
          <button class="btn btn-ghost text-primary" disabled={!online} onClick={() => setMode({ name: "new" })}>
            <Icon name="plus" size={20} /> Nuevo empleado
          </button>
        } />
      {!online && <Banner tone="warn">Administrar empleados necesita internet.</Banner>}
      {error && <Banner tone="error">{error}</Banner>}
      {busy && !members.length ? <Loading /> : (
        <>
          <Section title={`Con acceso a esta caja (${withAccess.length})`}>
            {withAccess.length ? withAccess.map(row) : <div class="row label-2 text-sm">Nadie todavía.</div>}
          </Section>
          {others.length > 0 && (
            <details class="flex flex-col gap-2">
              <summary class="text-sm label-2 font-semibold uppercase tracking-wide px-4 cursor-pointer">
                Otros empleados de la tienda, sin acceso a esta caja ({others.length})
              </summary>
              <div class="grouped mt-2">{others.map(row)}</div>
            </details>
          )}
          {archived.length > 0 && (
            <details class="flex flex-col gap-2">
              <summary class="text-sm label-2 font-semibold uppercase tracking-wide px-4 cursor-pointer">Dados de baja ({archived.length})</summary>
              <div class="grouped mt-2">
                {archived.map((member) => (
                  <div key={member.id} class="row">
                    <span class="flex-1 label-2">{member.name}</span>
                    <button class="btn btn-sm" disabled={!online} onClick={() => setMode({ name: "edit", member })}>Reactivar</button>
                  </div>
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}

function MemberForm({ member, self = false, onCancel, onSaved }: {
  member?: StaffMember;
  self?: boolean;
  onCancel: () => void;
  onSaved: () => Promise<void>;
}) {
  const { client, setup } = usePos();
  const [name, setName] = useState(member?.name ?? "");
  const [role, setRole] = useState<StaffRole>(member?.role ?? "cashier");
  const [pin, setPin] = useState("");
  const [pin2, setPin2] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lockedRole = !!member?.odoo_manager || self;
  const archived = member ? !member.active : false;

  async function run(task: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await task();
      await onSaved();
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  function submit(event: Event) {
    event.preventDefault();
    if (!name.trim()) return setError("Escribe el nombre.");
    if (!member || pin) {
      if (!/^\d{4,8}$/.test(pin)) return setError("El PIN debe tener de 4 a 8 números.");
      if (pin !== pin2) return setError("Los dos PIN no coinciden.");
    }
    void run(() => member
      ? client.changeStaff(setup.register.id, member.id, {
        ...(name.trim() !== member.name ? { name: name.trim() } : {}),
        ...(!lockedRole && role !== member.role ? { role } : {}),
        ...(pin ? { pin } : {}),
        ...(archived ? { active: true } : {}),
      })
      : client.addStaff(setup.register.id, { name: name.trim(), role, pin }));
  }

  return (
    <section class="p-4 lg:p-8 max-w-md mx-auto flex flex-col gap-6 rise">
      <PageHeader title={member ? (archived ? "Reactivar" : "Editar empleado") : "Nuevo empleado"} subtitle={member?.name}
        icon="idcard" back={onCancel} backLabel="Empleados" />
      <form class="flex flex-col gap-6" onSubmit={submit}>
        <Field label="Nombre">
          <input class="input w-full" type="text" maxLength={100} value={name} onInput={(e) => setName(e.currentTarget.value)} />
        </Field>
        <Section title="Acceso a esta caja"
          footer={lockedRole ? (self ? "No puedes cambiar tu propio acceso." : "Es gerente del POS en Odoo: su acceso se cambia en Odoo.") : undefined}>
          {ROLES.filter((r) => member || r.value !== "none").map((r) => (
            <label key={r.value} class={`row !items-start !py-3 ${lockedRole ? "opacity-60" : "cursor-pointer"}`}>
              <input type="radio" class="sr-only" name="role" value={r.value} checked={role === r.value}
                disabled={lockedRole} onChange={() => setRole(r.value)} />
              <span class="flex-1">
                <span class="font-semibold block">{r.label}</span>
                <span class="text-xs label-2">{r.help}</span>
              </span>
              {role === r.value && <Icon name="check" size={20} class="text-primary mt-1" />}
            </label>
          ))}
        </Section>
        <Field label={member ? "Nuevo PIN" : "PIN"} hint={member ? "Déjalo vacío para no cambiarlo." : "De 4 a 8 números."}>
          <div class="grid grid-cols-2 gap-2">
            <input class="input w-full tracking-[0.3em]" type="password" inputMode="numeric" autocomplete="new-password" maxLength={8}
              value={pin} onInput={(e) => setPin(e.currentTarget.value.replace(/\D/g, ""))} placeholder="PIN" aria-label="PIN" />
            <input class="input w-full tracking-[0.3em]" type="password" inputMode="numeric" autocomplete="new-password" maxLength={8}
              value={pin2} onInput={(e) => setPin2(e.currentTarget.value.replace(/\D/g, ""))} placeholder="Repite el PIN" aria-label="Repite el PIN" />
          </div>
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
        <button class="btn btn-primary btn-xl" disabled={busy}>{busy ? <span class="loading loading-spinner" /> : "Guardar"}</button>
      </form>
      {member && member.active && !self && (
        <button class="btn btn-ghost text-danger" disabled={busy}
          onClick={() => { if (confirm(`¿Dar de baja a ${member.name}? Ya no podrá entrar a ninguna caja. Su historial se conserva y se puede reactivar.`)) void run(() => client.changeStaff(setup.register.id, member.id, { active: false })); }}>
          <Icon name="trash" size={18} /> Dar de baja
        </button>
      )}
    </section>
  );
}
