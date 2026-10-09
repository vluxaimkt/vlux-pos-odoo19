import { useEffect, useRef, useState } from "preact/hooks";

import { getMeta, setMeta } from "../db/db";
import {
  choosePort, DEFAULT_SCALE, readWeight, rememberedPort, type ScaleReading, type ScaleSettings, type SerialLike, serialSupported,
} from "../input/scale";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { Banner } from "../ui/Page";

const META_SCALE = "scale_settings";

/**
 * The scale wired to this computer, inside the weigh dialog: read it (also
 * on opening, once a scale was connected here), connect it, and tune its
 * settings. Nothing shows where Web Serial is missing (phones, Safari).
 */
export function ScalePanel({ onWeight }: { onWeight: (kilos: number) => void }) {
  const { db } = usePos();
  const [port, setPort] = useState<SerialLike | null>(null);
  const [settings, setSettings] = useState<ScaleSettings>(DEFAULT_SCALE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<ScaleReading | null>(null);
  const [tuning, setTuning] = useState(false);
  const autoRead = useRef(false);

  async function read(target = port, using = settings) {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const reading = await readWeight(target, using);
      setLast(reading);
      if (reading.kilos > 0) onWeight(reading.kilos);
      if (!reading.stable) setError("La báscula dice que el peso aún se mueve: vuelve a leer cuando se quede quieto.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!serialSupported()) return;
    void (async () => {
      const saved = { ...DEFAULT_SCALE, ...((await getMeta<Partial<ScaleSettings>>(db, META_SCALE)) ?? {}) };
      setSettings(saved);
      const known = await rememberedPort().catch(() => null);
      setPort(known);
      if (known && !autoRead.current) {
        autoRead.current = true;
        await read(known, saved);
      }
    })();
  }, []);

  if (!serialSupported()) return null;

  async function connect() {
    setError(null);
    try {
      const chosen = await choosePort();
      setPort(chosen);
      await read(chosen);
    } catch (err) {
      // Closing the browser's picker is not an error worth showing.
      if (err instanceof Error && err.name !== "NotFoundError") setError(err.message);
    }
  }

  function change(next: Partial<ScaleSettings>) {
    const merged = { ...settings, ...next };
    setSettings(merged);
    void setMeta(db, META_SCALE, merged);
  }

  return (
    <div class="flex flex-col gap-2">
      <div class="flex items-center gap-2">
        {port ? (
          <button type="button" class="btn btn-primary flex-1" disabled={busy} onClick={() => void read()}>
            {busy ? <span class="loading loading-spinner" /> : <><Icon name="scale" size={18} /> Leer báscula</>}
          </button>
        ) : (
          <button type="button" class="btn flex-1" onClick={() => void connect()}>
            <Icon name="link" size={18} /> Conectar báscula
          </button>
        )}
        <button type="button" class="btn btn-ghost btn-square text-primary" aria-label="Ajustes de la báscula" title="Ajustes de la báscula"
          aria-expanded={tuning} onClick={() => setTuning(!tuning)}>
          <Icon name="gear" size={20} />
        </button>
      </div>
      {error && <Banner tone="warn">{error}</Banner>}
      {tuning && (
        <div class="grouped">
          <label class="row">
            <span class="flex-1 text-sm">Velocidad</span>
            <select class="select select-sm w-auto" value={String(settings.baudRate)}
              onChange={(e) => change({ baudRate: Number(e.currentTarget.value) })}>
              {[1200, 2400, 4800, 9600, 19200, 38400].map((rate) => <option key={rate} value={rate}>{rate}</option>)}
            </select>
          </label>
          <label class="row">
            <span class="flex-1 text-sm">Paridad / bits</span>
            <select class="select select-sm w-auto" value={`${settings.parity}-${settings.dataBits}`}
              onChange={(e) => {
                const [parity, bits] = e.currentTarget.value.split("-");
                change({ parity: parity as ScaleSettings["parity"], dataBits: Number(bits) as 7 | 8 });
              }}>
              <option value="none-8">Ninguna, 8 bits</option>
              <option value="even-7">Par, 7 bits</option>
              <option value="odd-7">Impar, 7 bits</option>
            </select>
          </label>
          <label class="row">
            <span class="flex-1 text-sm">Comando para pedir el peso</span>
            <input class="input input-sm w-24 font-mono" value={settings.requestCommand} maxLength={8} placeholder="(ninguno)"
              onInput={(e) => change({ requestCommand: e.currentTarget.value })} />
          </label>
          <div class="row text-xs label-2">
            <span class="flex-1">Lo último que mandó</span>
            <span class="font-mono truncate max-w-[12rem]">{last?.raw ?? "—"}</span>
          </div>
          {port && (
            <button type="button" class="row text-sm text-primary" onClick={() => void connect()}>Cambiar de puerto</button>
          )}
        </div>
      )}
    </div>
  );
}
