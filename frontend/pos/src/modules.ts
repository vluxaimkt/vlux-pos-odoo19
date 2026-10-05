import type { Employee, RegisterOptions } from "./api/types";

/**
 * The register's modules: one list for the sidebar, the padlocks and who may
 * authorize each one. A new module is a new entry here (open/closed); who may
 * use it reads the store's options, never a rule for one client. The server
 * checks every action again.
 */
export type ModuleId = "sell" | "sales" | "customers" | "cash" | "closing" | "staff" | "owner" | "queue";

export interface AccessContext {
  /** The register works with employees and PINs (pos_hr). */
  employeeLogin: boolean;
  options?: RegisterOptions;
  sessionOpen: boolean;
}

export interface ModuleDef {
  id: ModuleId;
  label: string;
  icon: string;
  /** Keyboard shortcut (F1…). */
  key: string;
  /** Who may use it; others see a padlock and ask someone allowed for their PIN. */
  allows: (employee: Employee | null, ctx: AccessContext) => boolean;
  /** Only with the register open (cash moves, closing). */
  needsSession?: boolean;
  /** Only on registers with employee login (employees, owner). */
  needsEmployees?: boolean;
}

const anyone = () => true;
const manager = (e: Employee | null) => e?.role === "manager";

export const MODULES: ModuleDef[] = [
  { id: "sell", label: "Vender", icon: "🛒", key: "F1", allows: anyone },
  { id: "sales", label: "Ventas y devoluciones", icon: "🧾", key: "F2", allows: anyone },
  { id: "customers", label: "Clientes y crédito", icon: "👥", key: "F3", allows: anyone },
  {
    id: "cash", label: "Entradas y salidas", icon: "💵", key: "F4", needsSession: true,
    // Managers move cash; cashiers take it out where the store allows it.
    allows: (e, ctx) => manager(e) || (e?.role === "cashier" && !!ctx.options?.cashier_cash_out),
  },
  { id: "closing", label: "Corte de caja", icon: "🧮", key: "F5", needsSession: true, allows: anyone },
  { id: "staff", label: "Empleados", icon: "🪪", key: "F6", needsEmployees: true, allows: (e) => !!e?.can_manage_staff },
  { id: "owner", label: "Dueño", icon: "👑", key: "F7", needsEmployees: true, allows: (e) => !!e?.is_owner },
  { id: "queue", label: "Ventas por enviar", icon: "📤", key: "F8", allows: anyone },
];

export function moduleById(id: ModuleId): ModuleDef {
  return MODULES.find((m) => m.id === id) ?? MODULES[0]!;
}

/** Whether the module appears at all on this register. */
export function isAvailable(def: ModuleDef, ctx: AccessContext): boolean {
  return !def.needsEmployees || ctx.employeeLogin;
}

/** Whether the person at the register may open it without someone else's PIN. */
export function canOpen(def: ModuleDef, employee: Employee | null, ctx: AccessContext): boolean {
  if (!ctx.employeeLogin) return !def.needsEmployees;
  return def.allows(employee, ctx);
}

/** Who may authorize a module for someone else (never the person asking). */
export function authorizersFor(def: ModuleDef, employees: Employee[], ctx: AccessContext, current: Employee | null): Employee[] {
  return employees.filter((e) => e.id !== current?.id && def.allows(e, ctx) && !!e.pin_sha1);
}
