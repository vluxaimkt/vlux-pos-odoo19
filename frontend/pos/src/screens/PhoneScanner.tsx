import { useEffect, useState } from "preact/hooks";

import { formatTime } from "../lib/locale";
import { getMeta, setMeta } from "../db/db";
import { type PhonePairing, PhoneScannerSource } from "../input/phoneScanner";
import type { ScanHandler } from "../input/sources";
import { usePos } from "../state";
import { explain } from "./SetupScreen";

const META_DEVICE = "device-id";
const META_PHONE = "phone-scanner";

// The link survives a reload; one per register device.
let current: PhonePairing | null = null;
let loaded = false;
const listeners = new Set<(pairing: PhonePairing | null) => void>();

function publish(pairing: PhonePairing | null) {
  current = pairing;
  for (const listener of listeners) listener(pairing);
}

/** Forget the link in memory (unpairing the device wipes the stored one). */
export function forgetPhoneScanner(): void {
  publish(null);
}

function usePairing(): [PhonePairing | null, (pairing: PhonePairing | null) => void] {
  const { db } = usePos();
  const [pairing, setPairing] = useState(current);
  useEffect(() => {
    listeners.add(setPairing);
    if (!loaded) {
      loaded = true;
      void getMeta<PhonePairing>(db, META_PHONE).then((kept) => kept && publish(kept));
    }
    return () => { listeners.delete(setPairing); };
  }, [db]);
  const save = (next: PhonePairing | null) => {
    publish(next);
    void setMeta(db, META_PHONE, next);
  };
  return [pairing, save];
}

async function deviceId(db: Parameters<typeof getMeta>[0]): Promise<string> {
  const kept = await getMeta<string>(db, META_DEVICE);
  if (kept) return kept;
  const fresh = crypto.randomUUID();
  await setMeta(db, META_DEVICE, fresh);
  return fresh;
}

/** While a phone is linked, its reads go to `handler` like any other scanner. */
export function usePhoneScanner(handler: ScanHandler) {
  const { client, setup, online } = usePos();
  const [pairing, save] = usePairing();
  useEffect(() => {
    if (!pairing || !online) return;
    const source = new PhoneScannerSource(client, setup.register.id, pairing, () => save(null));
    return source.start(handler);
  }, [pairing?.pairingId, online, client]);
}

type Step =
  | { name: "closed" }
  | { name: "linking"; code: string; qr: string; url: string; expiresAt: string }
  | { name: "linked" };

/** "Escáner del celular": link a phone by QR, see it linked, unlink it (as in the Odoo POS). */
export function PhoneScannerButton() {
  const { db, client, setup, online } = usePos();
  const [pairing, save] = usePairing();
  const [step, setStep] = useState<Step>({ name: "closed" });
  const [waiting, setWaiting] = useState<PhonePairing | null>(null);
  const [lastSeen, setLastSeen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // While the QR is on screen, watch for the phone to scan it.
  useEffect(() => {
    if (step.name !== "linking" || !waiting) return;
    const timer = setInterval(() => {
      client.scannerStatus(setup.register.id, waiting).then(({ status, last_seen_at }) => {
        if (status === "paired") {
          save(waiting);
          setLastSeen(last_seen_at);
          setStep({ name: "linked" });
        } else if (status !== "waiting") {
          setError("El código venció. Genera otro.");
          setStep({ name: "closed" });
        }
      }).catch(() => undefined);
    }, 2000);
    return () => clearInterval(timer);
  }, [step.name, waiting?.pairingId]);

  async function link() {
    setBusy(true);
    setError(null);
    try {
      const device = await deviceId(db);
      const answer = await client.scannerPair(setup.register.id, device);
      setWaiting({ pairingId: answer.pairing_id, deviceId: device });
      setStep({ name: "linking", code: answer.code, qr: answer.qr_data_uri, url: answer.scanner_url, expiresAt: answer.expires_at });
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }

  async function open() {
    setError(null);
    if (!pairing) return void link();
    setStep({ name: "linked" });
    try {
      const { status, last_seen_at } = await client.scannerStatus(setup.register.id, pairing);
      setLastSeen(last_seen_at);
      if (status !== "paired") {
        save(null);
        setStep({ name: "closed" });
        setError("El celular ya no está vinculado.");
      }
    } catch {
      // Offline: show what is known.
    }
  }

  async function unlink() {
    if (pairing) await client.scannerRevoke(setup.register.id, pairing).catch(() => undefined);
    save(null);
    setStep({ name: "closed" });
  }

  return (
    <>
      <button class={`btn btn-sm ${pairing ? "btn-success" : "btn-ghost"}`} disabled={busy || (!online && !pairing)}
        onClick={() => void open()} title="Usar un celular como escáner">
        {busy ? <span class="loading loading-spinner loading-xs" /> : pairing ? "📱 Celular vinculado" : "📱 Escáner del celular"}
      </button>
      {error && step.name === "closed" && <span role="alert" class="text-sm text-error ml-2">{error}</span>}
      {step.name !== "closed" && (
        <dialog class="modal modal-open" aria-label="Escáner del celular">
          <div class="modal-box flex flex-col gap-3 items-center text-center">
            {step.name === "linking" ? (
              <>
                <h3 class="text-lg font-bold">Escanea este código con el celular</h3>
                <img src={step.qr} alt="Código QR para vincular el celular" class="w-56 h-56 bg-white p-2 rounded" />
                <div>o abre en el celular <span class="font-mono text-xs break-all">{step.url}</span></div>
                <div>Código: <span class="font-mono text-xl tracking-widest">{step.code}</span></div>
                <div class="text-xs opacity-60">Vence a las {formatTime(step.expiresAt)}</div>
                <span class="loading loading-dots" aria-label="Esperando al celular" />
              </>
            ) : (
              <>
                <h3 class="text-lg font-bold">Celular vinculado ✓</h3>
                <p>Lo que escanees con el celular entra a esta venta.</p>
                {lastSeen && <p class="text-xs opacity-60">Última señal: {formatTime(lastSeen)}</p>}
                <button class="btn btn-warning btn-sm" onClick={() => void unlink()}>Desvincular celular</button>
              </>
            )}
            {error && <div role="alert" class="alert alert-error">{error}</div>}
            <div class="modal-action">
              <button class="btn" onClick={() => setStep({ name: "closed" })}>Cerrar</button>
            </div>
          </div>
        </dialog>
      )}
    </>
  );
}
