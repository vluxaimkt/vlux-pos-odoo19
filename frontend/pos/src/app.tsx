import { useCallback, useEffect, useMemo, useState } from "preact/hooks";

import { ApiClient, ApiError } from "./api/client";
import type { Employee, RegisterState } from "./api/types";
import { getMeta, type PosDb, setMeta } from "./db/db";
import { useInterval, useOnline } from "./hooks";
import { LoginScreen } from "./screens/LoginScreen";
import { RegisterScreen } from "./screens/RegisterScreen";
import { SellScreen } from "./screens/SellScreen";
import { SetupScreen } from "./screens/SetupScreen";
import { META_EMPLOYEES, META_SETUP, META_TOKEN, PosContext, type PosContextValue, type Setup, usePos } from "./state";
import { syncFeed } from "./sync/catalog";
import { flushOutbox } from "./sync/outbox";

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
  return <Register db={db} paired={paired} onForget={() => setPaired(null)} />;
}

function Register({ db, paired, onForget }: { db: PosDb; paired: Paired; onForget: () => void }) {
  const online = useOnline();
  const client = useMemo(() => new ApiClient({ token: paired.token }), [paired.token]);
  const registerId = paired.setup.register.id;
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employee, setEmployee] = useState<Employee | null>(null);
  const [registerState, setRegisterState] = useState<RegisterState | null>(null);
  const [catalogReady, setCatalogReady] = useState(false);
  const [status, setStatus] = useState({ syncing: false, pending: 0, attention: 0, error: null as string | null });

  // Unpairing wipes everything this device knows about the store (token,
  // catalog, customers, employees and their PIN hashes). Sales not yet sent
  // stay: they are the store's money and go out when a register is paired again.
  const forget = useCallback(async () => {
    await db.transaction("rw", db.meta, db.products, db.customers, async () => {
      await db.meta.clear();
      await db.products.clear();
      await db.customers.clear();
    });
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
  }, [employee, registerState?.employee_login]);

  // Cached copies first, so the register works from the first second offline.
  useEffect(() => {
    void (async () => {
      setEmployees((await getMeta<Employee[]>(db, META_EMPLOYEES)) ?? []);
      setRegisterState((await getMeta<RegisterState>(db, META_REGISTER_STATE)) ?? null);
      setCatalogReady((await db.products.count()) > 0);
    })();
  }, [db]);

  const applyRegisterState = useCallback((state: RegisterState) => {
    setRegisterState(state);
    void setMeta(db, META_REGISTER_STATE, state);
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
      await syncFeed(client, db, "products");
      await syncFeed(client, db, "customers");
      await refreshRegister();
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
  }, [client, db, refreshRegister]);

  const flush = useCallback(async () => {
    if (online) await flushOutbox(client, db);
    const pending = await db.outbox.where("status").equals("pending").count();
    const attention = await db.outbox.where("status").equals("attention").count();
    setStatus((s) => ({ ...s, pending, attention }));
  }, [client, db, online]);

  useInterval(syncCatalog, CATALOG_EVERY_MS, online);
  useInterval(flush, OUTBOX_EVERY_MS);

  const context: PosContextValue = {
    db, client, setup: paired.setup, employees, employee, online, setEmployee, forget,
  };

  const waiting = online ? null : "Conéctate a internet para la primera carga de esta caja.";
  let body;
  if (!catalogReady) body = <Splash text={waiting ?? "Descargando catálogo…"} />;
  else if (!registerState) body = <Splash text={waiting ?? "Consultando la caja…"} />;
  else if (registerState.employee_login && !employee) body = <LoginScreen />;
  else body = <Opened state={registerState} onOpened={applyRegisterState} />;

  return (
    <PosContext.Provider value={context}>
      <div class="min-h-screen flex flex-col bg-base-200">
        <Header status={status} />
        {status.error && <div role="alert" class="alert alert-error rounded-none">{status.error}</div>}
        <main class="flex-1">{body}</main>
      </div>
    </PosContext.Provider>
  );
}

function Opened({ state, onOpened }: { state: RegisterState; onOpened: (state: RegisterState) => void }) {
  return state.session?.state === "opened" ? <SellScreen /> : <RegisterScreen state={state} onOpened={onOpened} />;
}

function Header({ status }: { status: { syncing: boolean; pending: number; attention: number } }) {
  const { setup, employee, online, setEmployee, forget } = usePos();
  return (
    <header class="navbar bg-base-100 shadow-sm gap-2">
      <div class="flex-1 flex flex-col items-start">
        <span class="font-bold">{setup.register.name}</span>
        <span class="text-xs opacity-60">{setup.store.company.name}</span>
      </div>
      {status.attention > 0 && <span class="badge badge-error">{status.attention} por revisar</span>}
      {status.pending > 0 && <span class="badge badge-warning">{status.pending} por enviar</span>}
      {status.syncing && <span class="loading loading-dots loading-sm" aria-label="Sincronizando" />}
      <span class={`badge ${online ? "badge-success" : "badge-neutral"}`}>{online ? "En línea" : "Sin internet"}</span>
      <div class="dropdown dropdown-end">
        <button class="btn btn-ghost btn-sm" tabIndex={0}>{employee?.name ?? "Menú"}</button>
        <ul tabIndex={0} class="dropdown-content menu bg-base-100 rounded-box z-10 w-56 p-2 shadow">
          {employee && <li><button onClick={() => setEmployee(null)}>Cambiar de empleado</button></li>}
          <li><button onClick={() => { if (confirm("¿Desvincular este equipo? Se borran de este equipo el token, el catálogo, los clientes y los empleados. Las ventas por enviar se conservan.")) void forget(); }}>Desvincular equipo</button></li>
        </ul>
      </div>
    </header>
  );
}

function Splash({ text = "Cargando…" }: { text?: string }) {
  return (
    <div class="min-h-[60vh] grid place-items-center gap-2 text-center p-4">
      <span class="loading loading-spinner loading-lg" />
      <p class="opacity-70">{text}</p>
    </div>
  );
}
