import { useEffect, useRef, useState } from "preact/hooks";

import { downsizeImage } from "../lib/photo";
import { usePos } from "../state";
import { usePhonePairing } from "./PhoneScanner";
import { explain } from "./SetupScreen";
import { Icon } from "../ui/Icon";

const POLL_MS = 1500;
const WAIT_MS = 10 * 60 * 1000;

/**
 * A product picture: taken with this device (camera on a phone or tablet, a
 * file on a PC) or asked of the phone linked as scanner, which shows the
 * request, takes it and sends it back here. `value` is the picture to show
 * (a data URI, an existing picture URL or null).
 */
export function PhotoPicker({ value, onChange, barcode, label, canRemove = false }: {
  value: string | null;
  onChange: (image: string | null) => void;
  barcode?: string;
  label?: string;
  canRemove?: boolean;
}) {
  const { client, setup, online } = usePos();
  const pairing = usePhonePairing();
  const input = useRef<HTMLInputElement>(null);
  const [waiting, setWaiting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // While waiting for the phone, ask the server for the picture until it arrives.
  useEffect(() => {
    if (!waiting || !pairing) return;
    const started = Date.now();
    let stopped = false;
    const timer = setInterval(() => {
      if (Date.now() - started > WAIT_MS) {
        setWaiting(null);
        setError("El celular no mandó la foto a tiempo.");
        return;
      }
      client.scannerPhoto(setup.register.id, pairing.deviceId, waiting).then((answer) => {
        if (stopped) return;
        if (answer.status === "uploaded" && answer.image) {
          onChange(answer.image);
          setWaiting(null);
        } else if (answer.status !== "requested") {
          setWaiting(null);
          setError("El pedido de foto se canceló o venció.");
        }
      }).catch(() => undefined);
    }, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [waiting, pairing?.pairingId]);

  // Closing the form while waiting: the phone stops asking.
  const pending = useRef<string | null>(null);
  pending.current = waiting;
  useEffect(() => () => {
    if (pending.current && pairing) void client.scannerPhotoCancel(setup.register.id, pairing.deviceId, pending.current).catch(() => undefined);
  }, []);

  async function picked() {
    const file = input.current?.files?.[0];
    if (!file) return;
    setError(null);
    try {
      onChange(await downsizeImage(file));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (input.current) input.current.value = "";
    }
  }

  async function askPhone() {
    if (!pairing) return;
    setError(null);
    try {
      const { request_id } = await client.scannerPhotoRequest(setup.register.id, pairing, barcode, label);
      setWaiting(request_id);
    } catch (err) {
      setError(explain(err));
    }
  }

  async function stopWaiting() {
    if (waiting && pairing) await client.scannerPhotoCancel(setup.register.id, pairing.deviceId, waiting).catch(() => undefined);
    setWaiting(null);
  }

  return (
    <fieldset class="fieldset">
      <legend class="fieldset-legend">Foto</legend>
      {value
        ? <img src={value} alt="Foto del producto" class="max-h-40 object-contain self-center rounded bg-white" />
        : !waiting && <p class="text-xs opacity-60">Sin foto.</p>}
      {waiting ? (
        <div class="flex items-center gap-2">
          <span class="loading loading-dots" />
          <span class="flex-1 text-sm">Esperando la foto del celular… (en el celular aparece "La caja pide una foto")</span>
          <button type="button" class="btn btn-xs" onClick={() => void stopWaiting()}>Cancelar</button>
        </div>
      ) : (
        <div class="flex flex-wrap gap-2">
          <input ref={input} type="file" accept="image/*" capture="environment" class="hidden" onChange={() => void picked()} />
          <button type="button" class="btn btn-sm" onClick={() => input.current?.click()}><Icon name="camera" size={18} /> Foto con este equipo</button>
          {pairing && (
            <button type="button" class="btn btn-sm" disabled={!online} onClick={() => void askPhone()}><Icon name="phone" size={18} /> Tomar con el celular</button>
          )}
          {value && canRemove && <button type="button" class="btn btn-sm btn-ghost" onClick={() => onChange(null)}>Quitar foto</button>}
        </div>
      )}
      {!pairing && <p class="text-xs opacity-60">Para tomarla con el celular, vincúlalo primero con "Escáner del celular".</p>}
      {error && <div role="alert" class="alert alert-error text-sm">{error}</div>}
    </fieldset>
  );
}
