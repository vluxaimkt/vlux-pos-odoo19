import { useEffect, useState } from "preact/hooks";

import { formatTime } from "../lib/locale";
import { getMeta, setMeta } from "../db/db";
import { type PhonePairing, PhoneScannerSource } from "../input/phoneScanner";
import type { ScanHandler } from "../input/sources";
import { usePos } from "../state";
import { explain } from "./SetupScreen";
import { Icon } from "../ui/Icon";
import { Banner } from "../ui/Page";

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

/** The phone linked to this register, if any (for asking it for a picture). */
export function usePhonePairing(): PhonePairing | null {
  return usePairing()[0];
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
      <button class={`btn h-14 rounded-[16px] px-4 border-[0.5px] border-[var(--glass-border)] ${pairing ? "btn-success" : "bg-base-100"}`} disabled={busy || (!online && !pairing)}
        onClick={() => void open()} title="Usar un celular como escáner" aria-label={pairing ? "Celular vinculado" : "Escáner del celular"}>
        {busy ? <span class="loading loading-spinner loading-xs" /> : <><Icon name="phone" size={22} /> <span class="hidden xl:inline">{pairing ? "Celular vinculado" : "Escáner del celular"}</span></>}
      </button>
      {error && step.name === "closed" && <span role="alert" class="text-sm text-error ml-2">{error}</span>}
      {step.name !== "closed" && (
        <dialog class="modal modal-open" aria-label="Escáner del celular">
          <div class="modal-box flex flex-col gap-4 items-center text-center">
            {step.name === "linking" ? (
              <>
                <span class="avatar-disc w-14 h-14" data-role="manager" aria-hidden="true"><Icon name="phone" size={28} /></span>
                <h3 class="text-2xl">Vincula el celular</h3>
                <p class="label-2 text-sm">Escanea este código con la cámara del celular.</p>
                <img src={step.qr} alt="Código QR para vincular el celular" class="w-56 h-56 bg-white p-4 rounded-[20px] shadow-lg" />
                <div class="flex flex-col gap-1">
                  <span class="text-xs label-2">O escribe este código en el celular</span>
                  <span class="font-mono text-3xl font-semibold tracking-[0.3em]">{step.code}</span>
                </div>
                <div class="text-xs label-2 break-all">{step.url}</div>
                <div class="flex items-center gap-2 text-sm label-2">
                  <span class="loading loading-dots loading-sm text-primary" aria-label="Esperando al celular" />
                  Esperando al celular · vence a las {formatTime(step.expiresAt)}
                </div>
              </>
            ) : (
              <>
                <span class="avatar-disc w-14 h-14 pop" style={{ background: "linear-gradient(180deg, #34c759, #248a3d)" }} aria-hidden="true">
                  <Icon name="check" size={28} />
                </span>
                <h3 class="text-2xl">Celular vinculado</h3>
                <p class="label-2">Lo que escanees con el celular entra a esta venta.</p>
                {lastSeen && <p class="text-xs label-2">Última señal: {formatTime(lastSeen)}</p>}
                <button class="btn btn-ghost text-danger" onClick={() => void unlink()}><Icon name="link" size={18} /> Desvincular celular</button>
              </>
            )}
            {error && <div class="w-full text-left"><Banner tone="error">{error}</Banner></div>}
            <div class="modal-action mt-0 self-stretch">
              <button class="btn btn-primary flex-1" onClick={() => setStep({ name: "closed" })}>Listo</button>
            </div>
          </div>
        </dialog>
      )}
    </>
  );
}
