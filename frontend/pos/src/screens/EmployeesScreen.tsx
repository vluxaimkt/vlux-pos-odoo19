import { useEffect, useState } from "preact/hooks";

import type { StaffMember, StaffRole } from "../api/types";
import { usePos } from "../state";
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
    <li key={member.id} class="list-row items-center">
      <div class="list-col-grow">
        <div class="font-semibold">
          {member.name}
          {member.owner && <span class="badge badge-sm badge-accent ml-2">Dueño</span>}
        </div>
        <div class="text-xs opacity-70">
          {member.has_pin ? "Con PIN" : member.role === "manager"
            ? <span class="text-warning">Sin PIN: un encargado necesita PIN para entrar</span>
            : "Sin PIN"}
          {member.user ? ` · usuario ${member.user}` : ""}
        </div>
      </div>
      <span class={`badge ${member.role === "manager" ? "badge-primary" : member.role === "none" ? "badge-ghost" : "badge-neutral"}`}>
        {roleLabel(member.role)}
      </span>
      <button class="btn btn-sm" disabled={!online} onClick={() => setMode({ name: "edit", member })}>Editar</button>
    </li>
  );
  return (
    <section class="p-4 max-w-3xl mx-auto flex flex-col gap-3">
      <div class="flex items-center gap-2">
        <h2 class="text-xl flex-1">Empleados</h2>
        <button class="btn btn-primary btn-sm" disabled={!online} onClick={() => setMode({ name: "new" })}>Nuevo empleado</button>
        <button class="btn btn-ghost btn-sm" onClick={onClose}>Volver a vender</button>
      </div>
      {!online && <div role="alert" class="alert alert-warning">Administrar empleados necesita internet.</div>}
      {error && <div role="alert" class="alert alert-error">{error}</div>}
      {busy && <span class="loading loading-spinner" />}
      <h3 class="font-semibold">Con acceso a esta caja ({withAccess.length})</h3>
      <ul class="list bg-base-100 rounded-box">{withAccess.map(row)}</ul>
      {others.length > 0 && (
        <details class="collapse collapse-arrow bg-base-100">
          <summary class="collapse-title text-sm">Otros empleados de la tienda, sin acceso a esta caja ({others.length})</summary>
          <ul class="collapse-content list">{others.map(row)}</ul>
        </details>
      )}
      {archived.length > 0 && (
        <details class="collapse collapse-arrow bg-base-100">
          <summary class="collapse-title text-sm">Dados de baja ({archived.length})</summary>
          <ul class="collapse-content list">
            {archived.map((member) => (
              <li key={member.id} class="list-row items-center">
                <div class="list-col-grow opacity-70">{member.name}</div>
                <button class="btn btn-xs" disabled={!online} onClick={() => setMode({ name: "edit", member })}>Reactivar…</button>
              </li>
            ))}
          </ul>
        </details>
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
    <section class="p-4 max-w-md mx-auto flex flex-col gap-3">
      <h2 class="text-xl">{member ? (archived ? `Reactivar a ${member.name}` : `Editar a ${member.name}`) : "Nuevo empleado"}</h2>
      <form class="flex flex-col gap-3" onSubmit={submit}>
        <fieldset class="fieldset">
          <legend class="fieldset-legend">Nombre</legend>
          <input class="input w-full" type="text" maxLength={100} value={name} onInput={(e) => setName(e.currentTarget.value)} />
        </fieldset>
        <fieldset class="fieldset">
          <legend class="fieldset-legend">Acceso a esta caja</legend>
          {lockedRole && (
            <p class="text-xs opacity-70">
              {self ? "No puedes cambiar tu propio acceso." : "Es gerente del POS en Odoo: su acceso se cambia en Odoo."}
            </p>
          )}
          {ROLES.filter((r) => member || r.value !== "none").map((r) => (
            <label key={r.value} class="flex gap-2 items-start cursor-pointer py-1">
              <input type="radio" class="radio radio-sm mt-1" name="role" value={r.value} checked={role === r.value}
                disabled={lockedRole} onChange={() => setRole(r.value)} />
              <span><span class="font-semibold">{r.label}</span><br /><span class="text-xs opacity-70">{r.help}</span></span>
            </label>
          ))}
        </fieldset>
        <fieldset class="fieldset">
          <legend class="fieldset-legend">{member ? "Nuevo PIN (déjalo vacío para no cambiarlo)" : "PIN (4 a 8 números)"}</legend>
          <input class="input w-full" type="password" inputMode="numeric" autocomplete="new-password" maxLength={8}
            value={pin} onInput={(e) => setPin(e.currentTarget.value.replace(/\D/g, ""))} placeholder="PIN" />
          <input class="input w-full" type="password" inputMode="numeric" autocomplete="new-password" maxLength={8}
            value={pin2} onInput={(e) => setPin2(e.currentTarget.value.replace(/\D/g, ""))} placeholder="Repite el PIN" />
        </fieldset>
        {error && <div role="alert" class="alert alert-error">{error}</div>}
        <div class="flex gap-2">
          <button type="button" class="btn" onClick={onCancel}>Cancelar</button>
          <button class="btn btn-primary flex-1" disabled={busy}>{busy ? <span class="loading loading-spinner" /> : "Guardar"}</button>
        </div>
      </form>
      {member && member.active && !self && (
        <button class="btn btn-ghost btn-sm text-error" disabled={busy}
          onClick={() => { if (confirm(`¿Dar de baja a ${member.name}? Ya no podrá entrar a ninguna caja. Su historial se conserva y se puede reactivar.`)) void run(() => client.changeStaff(setup.register.id, member.id, { active: false })); }}>
          Dar de baja
        </button>
      )}
    </section>
  );
}
