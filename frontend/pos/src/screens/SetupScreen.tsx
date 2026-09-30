import { useState } from "preact/hooks";

import { ApiClient, ApiError, NetworkError } from "../api/client";
import type { Me, RegisterConfig, StoreConfig } from "../api/types";
import { type PosDb, setMeta } from "../db/db";
import { META_SETUP, META_TOKEN, type Setup } from "../state";
import { META_TOKEN_INFO } from "../sync/token";

const NEEDED_SCOPES = ["system:read", "catalog:read", "orders:write", "session:manage"];
// Tokens are URL-safe base64 with a short prefix: anything else is a paste
// mistake (a password, a sentence) and is never sent to the server.
const TOKEN_SHAPE = /^[A-Za-z0-9_-]{20,200}$/;

/**
 * First run: paste the register's token (Ajustes → API VLUX → Emitir token,
 * choosing the register). The token decides which register this device is:
 * one that serves every register is refused (least privilege).
 */
export function SetupScreen({ db, onReady }: { db: PosDb; onReady: (token: string, setup: Setup) => void }) {
  const [token, setToken] = useState("");
  const [checked, setChecked] = useState<{ me: Me; store: StoreConfig; register: RegisterConfig } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function check(event: Event) {
    event.preventDefault();
    setError(null);
    if (!TOKEN_SHAPE.test(token.trim())) {
      setError("Eso no parece un token de la API VLUX. Cópialo completo desde Odoo.");
      return;
    }
    setBusy(true);
    try {
      const client = new ApiClient({ token: token.trim() });
      const me = await client.me();
      const missing = NEEDED_SCOPES.filter((scope) => !me.token.scopes.includes(scope));
      if (missing.length) {
        setError(`Al token le faltan permisos: ${missing.join(", ")}.`);
        return;
      }
      // Least privilege: a register token that could also edit the catalog or
      // read the owner's dashboard would do more damage if the device is lost.
      const extra = me.token.scopes.filter((scope) => !NEEDED_SCOPES.includes(scope));
      if (extra.length) {
        setError(`Este token tiene permisos de más (${extra.join(", ")}). Emite uno sólo con: ${NEEDED_SCOPES.join(", ")}.`);
        return;
      }
      if (!me.token.register_id) {
        setError("Este token no está atado a una caja. En Odoo, emite uno eligiendo la caja de este equipo.");
        return;
      }
      const store = await client.storeConfig();
      const register = store.registers.find((r) => r.id === me.token.register_id);
      if (!register) {
        setError("La caja de este token ya no existe o no está disponible.");
        return;
      }
      setChecked({ me, store, register });
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!checked) return;
    const setup: Setup = checked;
    const value = token.trim();
    await db.transaction("rw", db.meta, async () => {
      await setMeta(db, META_TOKEN, value);
      await setMeta(db, META_TOKEN_INFO, checked.me.token);
      await setMeta(db, META_SETUP, setup);
    });
    setToken("");
    // Ask the browser not to evict the local data (catalog, queued sales).
    void navigator.storage?.persist?.();
    onReady(value, setup);
  }

  return (
    <main class="min-h-screen grid place-items-center p-4 bg-base-200">
      <div class="card w-full max-w-md bg-base-100 shadow-xl">
        <div class="card-body gap-4">
          <h1 class="card-title text-2xl">VLUX POS</h1>
          {!checked ? (
            <form class="flex flex-col gap-3" onSubmit={check}>
              <p class="text-base-content/70">
                Pega el token de esta caja. Se emite en Odoo: Ajustes → API VLUX → Emitir token.
              </p>
              <input
                class="input input-bordered w-full font-mono"
                type="password"
                autocomplete="off"
                autocapitalize="off"
                spellcheck={false}
                maxLength={200}
                placeholder="Token de la caja"
                value={token}
                onInput={(event) => setToken(event.currentTarget.value)}
                required
              />
              {error && <div role="alert" class="alert alert-error">{error}</div>}
              <button class="btn btn-primary" type="submit" disabled={busy || !token.trim()}>
                {busy ? <span class="loading loading-spinner" /> : "Conectar"}
              </button>
            </form>
          ) : (
            <div class="flex flex-col gap-3">
              <p>
                Este equipo será <strong>{checked.register.name}</strong> de {checked.store.company.name}.
              </p>
              <button class="btn btn-primary btn-lg" onClick={() => void confirm()}>Usar este equipo como {checked.register.name}</button>
              <button class="btn btn-ghost" onClick={() => setChecked(null)}>Usar otro token</button>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

export function explain(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "INVALID_TOKEN" || error.code === "MISSING_TOKEN") return "El token no es válido o ya expiró.";
    return error.message;
  }
  if (error instanceof NetworkError) return error.message;
  return "Algo salió mal. Intenta de nuevo.";
}
