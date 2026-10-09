import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";

import { ApiClient, ApiError } from "./api/client";
import type { CreditRow, Employee, RegisterState } from "./api/types";
import { getMeta, type PosDb, setMeta } from "./db/db";
import { useInterval, useOnline } from "./hooks";
import { LoginScreen } from "./screens/LoginScreen";
import { RegisterScreen } from "./screens/RegisterScreen";
import { SellScreen } from "./screens/SellScreen";
import { SetupScreen } from "./screens/SetupScreen";
import type { TaxInfo } from "./sale/pricing";
import { CloseScreen } from "./screens/CloseScreen";
import { QueueScreen } from "./screens/QueueScreen";
import { META_CREDIT, META_EMPLOYEES, META_SETUP, META_TAXES, META_TOKEN, PosContext, type PosContextValue, type Setup, usePos } from "./state";
import { canAuthorizeCredit, canSellOnCredit } from "./sale/credit";
import { setLocale } from "./lib/locale";
import { CustomersScreen } from "./screens/CustomersScreen";
import { SalesScreen } from "./screens/SalesScreen";
import { CashMoveScreen } from "./screens/CashMoveScreen";
import { EmployeesScreen } from "./screens/EmployeesScreen";
import { dropImageUrls } from "./sync/images";
import { forgetPhoneScanner } from "./screens/PhoneScanner";
import { AuthorizeDialog } from "./screens/AuthorizeDialog";
import { OwnerScreen } from "./screens/OwnerScreen";
import { type AccessContext, authorizersFor, canOpen, isAvailable, type ModuleDef, type ModuleId, moduleById, MODULES } from "./modules";
import { syncFeed } from "./sync/catalog";
import { flushOutbox } from "./sync/outbox";
import { renewIfDue } from "./sync/token";
import { APP_ICON } from "./ui/brand";
import { ThemeSwitch } from "./ui/ThemeSwitch";
import { Icon } from "./ui/Icon";
import { Banner } from "./ui/Page";

const META_REGISTER_STATE = "register_state";
const CATALOG_EVERY_MS = 5 * 60_000;
const OUTBOX_EVERY_MS = 15_000;
// An unattended register goes back to the PIN keypad.
const IDLE_LOCK_MS = 5 * 60_000;
const ACTIVITY_EVENTS = ["pointerdown", "keydown", "wheel", "touchstart"] as const;

type Paired = { token: string; setup: Setup };

export function App({ db }: { db: PosDb }) {
  const [paired, setPaired] = useState<Paired | null | undefined>(undefined);

  useEffect(() => {
    void (async () => {
      const token = await getMeta<string>(db, META_TOKEN);
      const setup = await getMeta<Setup>(db, META_SETUP);
      setPaired(token && setup ? { token, setup } : null);
    })();
  }, [db]);

  if (paired === undefined) return <Splash />;
  if (!paired) return <SetupScreen db={db} onReady={(token, setup) => setPaired({ token, setup })} />;
  return (
    <Register
      db={db}
      paired={paired}
      onForget={() => setPaired(null)}
      onRenewed={(token) => setPaired((current) => (current ? { ...current, token } : current))}
      onSetup={(setup) => setPaired((current) => (current ? { ...current, setup } : current))}
    />
  );
}

function Register({ db, paired, onForget, onRenewed, onSetup }: {
  db: PosDb;
  paired: Paired;
  onForget: () => void;
  onRenewed: (token: string) => void;
  onSetup: (setup: Setup) => void;
}) {
  const online = useOnline();
  const client = useMemo(() => new ApiClient({ token: paired.token }), [paired.token]);
  const registerId = paired.setup.register.id;
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employee, setEmployeeState] = useState<Employee | null>(null);

  // Leaving the register (or the idle lock) ends the employee session on the
  // server too; a new token (renewal) means a new PIN, since sessions are tied to it.
  // Someone else's PIN opened one module (or one action) for the person at the register.
  const [authorization, setAuthorization] = useState<{ employee: Employee; purpose: string } | null>(null);
  const cashierSession = useRef<string | null>(null);

  /** End an authorization: its brief session ends and the cashier's comes back. */
  const releaseAuthorization = useCallback(() => {
    if (cashierSession.current !== null) {
      // The logout carries the brief session (still in the client), then the cashier's comes back.
      if (navigator.onLine) void client.employeeLogout(registerId).catch(() => undefined);
      client.employeeSession = cashierSession.current || null;
      cashierSession.current = null;
    }
    setAuthorization(null);
  }, [client, registerId]);

  const setEmployee = useCallback((next: Employee | null) => {
    if (cashierSession.current !== null) {
      // An authorization in course ends with the cashier's turn.
      if (navigator.onLine) void client.employeeLogout(registerId).catch(() => undefined);
      client.employeeSession = cashierSession.current || null;
      cashierSession.current = null;
      setAuthorization(null);
    }
    if (!next && client.employeeSession) {
      if (navigator.onLine) void client.employeeLogout(registerId).catch(() => undefined);
      client.employeeSession = null;
    }
    setEmployeeState(next);
  }, [client, registerId]);

  useEffect(() => {
    client.onPinRequired = () => setEmployee(null);
    return () => {
      client.onPinRequired = null;
    };
  }, [client, setEmployee]);

  useEffect(() => {
    setEmployeeState(null);
  }, [client]);
  const [registerState, setRegisterState] = useState<RegisterState | null>(null);
  const [catalogReady, setCatalogReady] = useState(false);
  const [taxes, setTaxes] = useState<Map<number, TaxInfo>>(new Map());
  const [credit, setCredit] = useState<Map<number, CreditRow>>(new Map());
  const [view, setViewState] = useState<ModuleId>("sell");
  // The modules visited before this one: "back" returns to the last that can still open.
  const [trail, setTrail] = useState<ModuleId[]>([]);
  const viewRef = useRef(view);
  viewRef.current = view;
  // A module waiting for someone's PIN (padlock).
  const [asking, setAsking] = useState<{ title: string; purpose: string; authorizers: Employee[]; resolve: (ok: boolean) => void } | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [status, setStatus] = useState({ syncing: false, pending: 0, attention: 0, error: null as string | null });

  // Unpairing wipes everything this device knows about the store (token,
  // catalog, customers, employees and their PIN hashes). Sales not yet sent
  // stay: they are the store's money and go out when a register is paired again.
  const forget = useCallback(async () => {
    await db.transaction("rw", [db.meta, db.products, db.customers, db.images], async () => {
      await db.meta.clear();
      await db.products.clear();
      await db.customers.clear();
      await db.images.clear();
    });
    dropImageUrls();
    forgetPhoneScanner();
    onForget();
  }, [db, onForget]);

  useEffect(() => {
    if (!employee || !registerState?.employee_login) return;
    let timer = setTimeout(() => setEmployee(null), IDLE_LOCK_MS);
    const activity = () => {
      clearTimeout(timer);
      timer = setTimeout(() => setEmployee(null), IDLE_LOCK_MS);
    };
    for (const name of ACTIVITY_EVENTS) window.addEventListener(name, activity, { passive: true });
    return () => {
      clearTimeout(timer);
      for (const name of ACTIVITY_EVENTS) window.removeEventListener(name, activity);
    };
  }, [employee, registerState?.employee_login, setEmployee]);

  // Cached copies first, so the register works from the first second offline.
  useEffect(() => {
    void (async () => {
      setEmployees((await getMeta<Employee[]>(db, META_EMPLOYEES)) ?? []);
      setRegisterState((await getMeta<RegisterState>(db, META_REGISTER_STATE)) ?? null);
      setTaxes(new Map(((await getMeta<TaxInfo[]>(db, META_TAXES)) ?? []).map((tax) => [tax.id, tax])));
      setCredit(new Map(((await getMeta<CreditRow[]>(db, META_CREDIT)) ?? []).map((row) => [row.partner_id, row])));
      setCatalogReady((await db.products.count()) > 0);
    })();
  }, [db]);

  const applyRegisterState = useCallback((state: RegisterState) => {
    setRegisterState(state);
    void setMeta(db, META_REGISTER_STATE, state);
  }, [db]);

  const storeCredit = useCallback((rows: Map<number, CreditRow>) => {
    setCredit(rows);
    void setMeta(db, META_CREDIT, [...rows.values()]);
  }, [db]);

  const refreshCredit = useCallback(async () => {
    const { items } = await client.creditCustomers();
    storeCredit(new Map(items.map((row) => [row.partner_id, row])));
  }, [client, storeCredit]);

  const saveCredit = useCallback((row: CreditRow) => {
    setCredit((current) => {
      const next = new Map(current);
      next.set(row.partner_id, row);
      void setMeta(db, META_CREDIT, [...next.values()]);
      return next;
    });
  }, [db]);

  const refreshRegister = useCallback(async () => {
    const [state, staff] = await Promise.all([client.registerState(registerId), client.employees(registerId)]);
    setRegisterState(state);
    setEmployees(staff.items);
    await setMeta(db, META_REGISTER_STATE, state);
    await setMeta(db, META_EMPLOYEES, staff.items);
  }, [client, db, registerId]);

  const syncCatalog = useCallback(async () => {
    setStatus((s) => ({ ...s, syncing: true }));
    try {
      // Renew the token before it ages out; the next round uses the new one.
      const renewed = await renewIfDue(client, db);
      if (renewed) onRenewed(renewed);
      // The store and this register's settings (payment methods, receipt
      // header…) change in Odoo: keep the paired copy current.
      const store = await client.storeConfig();
      const register = store.registers.find((r) => r.id === registerId);
      if (register) {
        const setup: Setup = { ...paired.setup, store, register };
        await setMeta(db, META_SETUP, setup);
        onSetup(setup);
      }
      await syncFeed(client, db, "products");
      await syncFeed(client, db, "customers");
      const { items } = await client.taxes();
      await setMeta(db, META_TAXES, items);
      setTaxes(new Map(items.map((tax) => [tax.id, tax])));
      await refreshRegister();
      await refreshCredit();
      setCatalogReady(true);
      setStatus((s) => ({ ...s, error: null }));
    } catch (error) {
      if (error instanceof ApiError && error.code === "INVALID_TOKEN") {
        setStatus((s) => ({ ...s, error: "El token de esta caja fue revocado. Vuelve a configurarla." }));
      }
      throw error;
    } finally {
      setStatus((s) => ({ ...s, syncing: false }));
    }
  }, [client, db, refreshRegister, refreshCredit, onRenewed, onSetup, paired.setup, registerId]);

  // One send at a time: the timer and "Enviar ahora" must not overlap
  // (a double send would be harmless, the uuid dedupes it, but wasteful).
  const flushing = useRef<Promise<unknown> | null>(null);
  const flush = useCallback(async () => {
    if (online) {
      flushing.current ??= flushOutbox(client, db).finally(() => { flushing.current = null; });
      await flushing.current;
    }
    const pending = await db.outbox.where("status").equals("pending").count();
    const attention = await db.outbox.where("status").equals("attention").count();
    setStatus((s) => ({ ...s, pending, attention }));
  }, [client, db, online]);

  useInterval(syncCatalog, CATALOG_EVERY_MS, online);
  useInterval(flush, OUTBOX_EVERY_MS);

  const access: AccessContext = {
    employeeLogin: !!registerState?.employee_login,
    options: registerState?.options,
    sessionOpen: registerState?.session?.state === "opened",
  };
  // Inside an authorized module, the screen acts as whoever authorized it.
  const acting = authorization?.employee ?? employee;

  /** Ask someone allowed for their PIN; resolves true once the server granted a brief session. */
  const requestAuthorization = useCallback((title: string, purpose: string, authorizers: Employee[]) => {
    return new Promise<boolean>((resolve) => setAsking({ title, purpose, authorizers, resolve }));
  }, []);

  async function authorize(person: Employee, pin: string) {
    if (!asking) return;
    const answer = await client.employeeAuthorize(registerId, person.id, pin, asking.purpose);
    cashierSession.current = client.employeeSession ?? "";
    client.employeeSession = answer.session;
    setAuthorization({ employee: person, purpose: asking.purpose });
    asking.resolve(true);
    setAsking(null);
  }

  /** Show a module, remembering the one left (unless going back). */
  function show(id: ModuleId, goingBack: boolean) {
    const current = viewRef.current;
    if (current === id) return;
    if (!goingBack) setTrail((t) => [...t.filter((m) => m !== id), current].slice(-10));
    setViewState(id);
  }

  /** Go to a module: directly if allowed, else after someone's PIN. Leaving one ends its authorization. */
  async function open(def: ModuleDef, goingBack = false) {
    setDrawer(false);
    if (!isAvailable(def, access) || (def.needsSession && !access.sessionOpen)) return;
    if (authorization && authorization.purpose !== def.id) releaseAuthorization();
    if (canOpen(def, employee, access) || authorization?.purpose === def.id) return show(def.id, goingBack);
    if (!online) return setStatus((s) => ({ ...s, error: "Para autorizar con PIN se necesita internet." }));
    const ok = await requestAuthorization(def.label, def.id, authorizersFor(def, employees, access, employee));
    if (ok) show(def.id, goingBack);
  }
  const setView = (id: ModuleId) => void open(moduleById(id));

  /** Where "back" goes: the last visited module that can still open (Vender if none), and the trail left. */
  function backTarget(): { id: ModuleId; rest: ModuleId[] } {
    const rest = [...trail];
    while (rest.length) {
      const id = rest.pop()!;
      const def = moduleById(id);
      if (id !== view && isAvailable(def, access) && !(def.needsSession && !access.sessionOpen)) return { id, rest };
    }
    return { id: "sell", rest };
  }
  const goBack = () => {
    const { id, rest } = backTarget();
    setTrail(rest);
    void open(moduleById(id), true);
  };

  // F1…F8 open the modules (as on many registers); not while a PIN is being typed.
  const openRef = useRef(open);
  openRef.current = open;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (asking || (registerState?.employee_login && !employee)) return;
      const def = MODULES.find((m) => m.key === event.key);
      if (!def) return;
      event.preventDefault();
      void openRef.current(def);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [asking, employee, registerState?.employee_login]);

  // Money and dates the way the store's language writes them.
  setLocale(paired.setup.store.company.locale);

  const context: PosContextValue = {
    db, client, setup: paired.setup, employees, employee: acting, online, registerState, taxes, setEmployee, forget,
    authorizedBy: authorization ? authorization.employee : null,
    requestAuthorization: (title, purpose, allowed) => requestAuthorization(title, purpose,
      employees.filter((e) => e.id !== employee?.id && !!e.pin_sha1 && allowed(e))),
    releaseAuthorization,
    back: { label: moduleById(backTarget().id).label, go: goBack },
    flushNow: flush,
    credit, saveCredit, refreshCredit,
    canSellOnCredit: canSellOnCredit(!!registerState?.employee_login, acting?.role, registerState?.options?.credit_sellers),
    canAuthorizeCredit: canAuthorizeCredit(!!registerState?.employee_login, acting?.role),
    canEditCatalog: !!registerState?.employee_login && !!acting?.can_edit_catalog,
  };

  const waiting = online ? null : "Conéctate a internet para la primera carga de esta caja.";
  let body;
  if (!catalogReady) body = <Splash text={waiting ?? "Descargando catálogo…"} />;
  else if (!registerState) body = <Splash text={waiting ?? "Consultando la caja…"} />;
  else if (registerState.employee_login && !employee) body = <LoginScreen />;
  else if (view === "queue") body = <QueueScreen onClose={goBack} />;
  else if (view === "customers") body = <CustomersScreen onClose={goBack} />;
  else if (view === "sales") body = <SalesScreen onClose={goBack} />;
  else if (view === "cash") body = <CashMoveScreen onClose={goBack} />;
  else if (view === "staff") body = <EmployeesScreen onClose={goBack} onChanged={refreshRegister} />;
  else if (view === "owner") body = <OwnerScreen onClose={goBack} onOptionsSaved={refreshRegister} />;
  else if (view === "closing") {
    body = (
      <CloseScreen
        onCancel={goBack}
        onQueue={() => setView("queue")}
        onClosed={(state) => {
          applyRegisterState(state);
          setView("sell");
        }}
      />
    );
  }
  else body = <Opened state={registerState} onOpened={applyRegisterState} />;
  const locked = !catalogReady || !registerState || (registerState.employee_login && !employee);

  return (
    <PosContext.Provider value={context}>
      <div class="min-h-screen flex app-backdrop">
        {!locked && (
          <Sidebar view={view} access={access} status={status} open={(def) => void open(def)} person={employee}
            drawer={drawer} onDrawer={setDrawer} authorization={authorization} />
        )}
        <div class="flex-1 min-w-0 flex flex-col">
          {!locked && (
            <div class="lg:hidden glass sticky top-0 z-30 h-14 px-2 flex items-center gap-1 rounded-none border-x-0 border-t-0 print:hidden">
              <button class="btn btn-ghost btn-square text-primary" aria-label="Abrir menú" onClick={() => setDrawer(true)}>
                <Icon name="menu" />
              </button>
              <span class="font-semibold flex-1 tracking-tight">{moduleById(view).label}</span>
              <span class={`pill ${online ? "pill-ok" : ""}`}>{online ? "En línea" : "Sin internet"}</span>
            </div>
          )}
          {locked && <TopBar status={status} />}
          {status.error && <div class="px-4 pt-4 lg:px-6 print:hidden"><Banner tone="error">{status.error}</Banner></div>}
          <main class="flex-1">{body}</main>
        </div>
        {asking && (
          <AuthorizeDialog title={asking.title} authorizers={asking.authorizers} onAuthorize={authorize}
            onCancel={() => { asking.resolve(false); setAsking(null); }} />
        )}
      </div>
    </PosContext.Provider>
  );
}

function Opened({ state, onOpened }: { state: RegisterState; onOpened: (state: RegisterState) => void }) {
  return state.session?.state === "opened" ? <SellScreen /> : <RegisterScreen state={state} onOpened={onOpened} />;
}

/** The modules, always in sight (padlock where someone's PIN is needed); a drawer on small screens. */
function Sidebar({ view, access, status, open, drawer, onDrawer, person, authorization }: {
  view: ModuleId;
  access: AccessContext;
  status: { syncing: boolean; pending: number; attention: number };
  open: (def: ModuleDef) => void;
  drawer: boolean;
  onDrawer: (open: boolean) => void;
  /** The person at the register: the padlocks are theirs, whoever authorized a module. */
  person: Employee | null;
  authorization: { employee: Employee; purpose: string } | null;
}) {
  const { setup, online, setEmployee, forget } = usePos();
  const authorizedBy = authorization?.employee ?? null;
  return (
    <>
      <button class={`fixed inset-0 z-30 lg:hidden bg-black/30 transition-opacity duration-300 ${drawer ? "opacity-100" : "opacity-0 pointer-events-none"}`}
        style={{ backdropFilter: "blur(4px)" }} tabIndex={-1} aria-label="Cerrar menú" onClick={() => onDrawer(false)} />
      <aside class={`glass print:hidden w-72 shrink-0 flex flex-col h-screen z-40 rounded-none border-y-0 border-l-0
        fixed inset-y-0 left-0 transition-[transform,visibility] duration-300 [transition-timing-function:cubic-bezier(0.4,0,0.2,1.4)]
        ${drawer ? "visible translate-x-0 shadow-2xl" : "invisible -translate-x-full"}
        lg:visible lg:translate-x-0 lg:sticky lg:top-0 lg:shadow-none`} aria-label="Módulos">
        <div class="px-4 pt-6 pb-4 flex flex-col gap-3">
          <div class="flex items-center gap-3">
            <img src={APP_ICON} alt="" width={40} height={40} class="rounded-[12px] shadow-sm" />
            <div class="min-w-0">
              <div class="font-semibold leading-tight truncate">{setup.register.name}</div>
              <div class="text-xs text-[var(--label-secondary)] truncate">{setup.store.company.name}</div>
            </div>
          </div>
          <div class="flex flex-wrap gap-2">
            <span class={`pill ${online ? "pill-ok" : ""}`}>{online ? "En línea" : "Sin internet"}</span>
            {status.pending > 0 && <span class="pill pill-warn">{status.pending} por enviar</span>}
            {status.attention > 0 && <span class="pill pill-bad">{status.attention} por revisar</span>}
            {status.syncing && <span class="loading loading-dots loading-xs text-primary" aria-label="Sincronizando" />}
          </div>
        </div>
        <nav class="flex-1 overflow-y-auto px-3 py-1">
          <ul class="flex flex-col gap-1">
            {MODULES.filter((def) => isAvailable(def, access)).map((def) => {
              const disabled = !!def.needsSession && !access.sessionOpen;
              const lockedModule = !disabled && !canOpen(def, person, access) && authorization?.purpose !== def.id;
              return (
                <li key={def.id}>
                  <button class="nav-item" disabled={disabled}
                    aria-current={view === def.id ? "page" : undefined}
                    title={disabled ? "Abre la caja primero" : lockedModule ? "Pide el PIN de alguien con permiso" : undefined}
                    onClick={() => open(def)}>
                    <Icon name={def.icon} size={22} />
                    <span class="flex-1 text-left">{def.label}</span>
                    {lockedModule && <Icon name="lock" size={16} class="opacity-60" />}
                    <span class="keycap" aria-hidden="true">{def.key}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>
        <div class="p-3 border-t border-[var(--hairline)] flex flex-col gap-2">
          {person && (
            <div class="flex items-center gap-3 px-1">
              <span class="avatar-disc w-10 h-10 text-base" data-role={person.role}>{(person.name.trim()[0] ?? "?").toUpperCase()}</span>
              <div class="min-w-0 flex-1">
                <div class="font-semibold leading-tight truncate">{person.name}</div>
                <div class="text-xs text-[var(--label-secondary)]">{authorizedBy ? `Autorizó ${authorizedBy.name} (sólo este módulo)` : person.role === "manager" ? "Encargado" : "Cajero"}</div>
              </div>
              <button class="btn btn-ghost btn-square text-primary" aria-label="Cambiar de empleado / bloquear" title="Cambiar de empleado / bloquear"
                onClick={() => setEmployee(null)}>
                <Icon name="lock" size={22} />
              </button>
            </div>
          )}
          <div class="flex items-center justify-between gap-2 px-1">
            <span class="text-xs label-2">Tema</span>
            <ThemeSwitch />
          </div>
          <button class="btn btn-ghost btn-sm justify-start text-[var(--label-secondary)] font-normal" onClick={() => {
            if (confirm("¿Desvincular este equipo? Se borran de este equipo el token, el catálogo, los clientes y los empleados. Las ventas por enviar se conservan.")) void forget();
          }}>
            <Icon name="link" size={16} /> Desvincular equipo
          </button>
        </div>
      </aside>
    </>
  );
}

/** Before anyone is at the register (catalog loading, PIN keypad): the register and the connection. */
function TopBar({ status }: { status: { syncing: boolean; pending: number } }) {
  const { setup, online } = usePos();
  return (
    <header class="glass sticky top-0 z-30 h-[72px] px-6 flex items-center gap-3 rounded-none border-x-0 border-t-0 print:hidden">
      <img src={APP_ICON} alt="" width={40} height={40} class="rounded-[12px] shadow-sm" />
      <div class="flex-1 min-w-0 flex flex-col items-start">
        <span class="font-semibold leading-tight truncate max-w-full">{setup.register.name}</span>
        <span class="text-xs text-[var(--label-secondary)] truncate max-w-full">{setup.store.company.name}</span>
      </div>
      {status.pending > 0 && <span class="pill pill-warn">{status.pending} por enviar</span>}
      {status.syncing && <span class="loading loading-dots loading-sm text-primary" aria-label="Sincronizando" />}
      <span class={`pill ${online ? "pill-ok" : ""}`}>{online ? "En línea" : "Sin internet"}</span>
      <ThemeSwitch class="hidden sm:flex" />
    </header>
  );
}

function Splash({ text = "Cargando…" }: { text?: string }) {
  return (
    <div class="min-h-[60vh] grid place-items-center content-center gap-4 text-center p-4 rise">
      <span class="loading loading-spinner loading-lg text-primary" />
      <p class="text-[var(--label-secondary)]">{text}</p>
    </div>
  );
}
