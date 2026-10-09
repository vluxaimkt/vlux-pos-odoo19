/**
 * Weight from a scale wired to the register's computer (serial / USB-serial),
 * read with Web Serial (Chrome or Edge on a desktop, over HTTPS). Scales send
 * a line of text with the weight, either continuously or when asked; the
 * protocol differs between brands and models (Torrey PCR-40T: RS-232, exact
 * format to confirm with the scale), so the reader is configurable per device:
 * speed, the command that asks for the weight, and a generic parser that takes
 * the number out of the line. The sale only sees `readWeight()`; typing the
 * weight by hand always remains.
 */

export interface ScaleSettings {
  /** Bits per second: 9600 is the usual default for retail scales. */
  baudRate: number;
  dataBits: 7 | 8;
  parity: "none" | "even" | "odd";
  stopBits: 1 | 2;
  /** Sent when the scale stays quiet (e.g. "P"); empty: only listen. */
  requestCommand: string;
  /** How long to wait for a weight, in milliseconds. */
  timeoutMs: number;
}

export const DEFAULT_SCALE: ScaleSettings = {
  baudRate: 9600, dataBits: 8, parity: "none", stopBits: 1, requestCommand: "P", timeoutMs: 2500,
};

export interface ScaleReading {
  /** Kilos, to the gram. */
  kilos: number;
  /** False when the scale says the weight is still moving (only some scales say it). */
  stable: boolean;
  /** The raw line, to tune the settings. */
  raw: string;
}

/**
 * The weight in a line a scale sent, or null. Takes the last number in the
 * line (scales put status codes before the weight: "ST,GS,+0001.250kg"), its
 * unit when given (g, lb or kg; kg by default) and a motion flag ("US",
 * "unstable", "?") when present.
 */
export function parseScaleLine(line: string): ScaleReading | null {
  const raw = line.replace(/[\x00-\x1f]/g, " ").trim();
  if (!raw) return null;
  const numbers = [...raw.matchAll(/([+-]?\d+(?:[.,]\d+)?)\s*(kg|g|lb)?/gi)];
  const last = numbers[numbers.length - 1];
  if (!last) return null;
  let value = Number(last[1]!.replace(",", "."));
  if (!Number.isFinite(value) || value < 0) return null;
  const unit = (last[2] ?? "kg").toLowerCase();
  if (unit === "g") value /= 1000;
  if (unit === "lb") value *= 0.45359237;
  const stable = !/\bUS\b|unstable|inestable|\?/i.test(raw);
  return { kilos: Math.round(value * 1000) / 1000, stable, raw };
}

/** The part of Web Serial the reader uses (navigator.serial ports implement it). */
export interface SerialLike {
  open(options: { baudRate: number; dataBits: number; parity: string; stopBits: number }): Promise<void>;
  close(): Promise<void>;
  readable: ReadableStream<Uint8Array> | null;
  writable: WritableStream<Uint8Array> | null;
}

/** Web Serial is there (Chrome/Edge desktop over HTTPS); phones and Safari don't have it. */
export function serialSupported(): boolean {
  return typeof navigator !== "undefined" && "serial" in navigator;
}

/** Ask the person to pick the scale's port (needs a click). */
export async function choosePort(): Promise<SerialLike> {
  const serial = (navigator as unknown as { serial: { requestPort(): Promise<SerialLike> } }).serial;
  return serial.requestPort();
}

/** The port picked before on this device, if the browser still allows it. */
export async function rememberedPort(): Promise<SerialLike | null> {
  if (!serialSupported()) return null;
  const serial = (navigator as unknown as { serial: { getPorts(): Promise<SerialLike[]> } }).serial;
  const ports = await serial.getPorts();
  return ports[0] ?? null;
}

/**
 * One weight from the port: listen; if nothing arrives in a third of the
 * time, send the request command; take the first line with a weight.
 * Opens and closes the port each time, so nothing stays locked.
 */
export async function readWeight(port: SerialLike, settings: ScaleSettings = DEFAULT_SCALE): Promise<ScaleReading> {
  await port.open({ baudRate: settings.baudRate, dataBits: settings.dataBits, parity: settings.parity, stopBits: settings.stopBits });
  const reader = port.readable?.getReader();
  if (!reader) {
    await port.close();
    throw new Error("La báscula no envía datos por este puerto.");
  }
  const decoder = new TextDecoder();
  let buffer = "";
  let lastRaw = "";
  const deadline = Date.now() + settings.timeoutMs;
  let asked = false;
  // One read in flight at a time: a read that outlived a wait still gets the next bytes.
  let pendingRead: Promise<ReadableStreamReadResult<Uint8Array>> | null = null;
  try {
    while (Date.now() < deadline) {
      if (!asked && settings.requestCommand && Date.now() > deadline - (settings.timeoutMs * 2) / 3) {
        asked = true;
        const writer = port.writable?.getWriter();
        if (writer) {
          await writer.write(new TextEncoder().encode(settings.requestCommand));
          writer.releaseLock();
        }
      }
      pendingRead ??= reader.read();
      const chunk = await Promise.race([
        pendingRead,
        new Promise<{ value?: undefined; done: false; timeout: true }>((resolve) =>
          setTimeout(() => resolve({ done: false, timeout: true }), Math.max(50, Math.min(300, deadline - Date.now())))),
      ]);
      if ("timeout" in chunk) continue;
      pendingRead = null;
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split(/\r\n|\r|\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.trim()) lastRaw = line;
        const reading = parseScaleLine(line);
        if (reading) return reading;
      }
      // Some scales never end the line: try what has arrived.
      const pending = parseScaleLine(buffer);
      if (pending && buffer.length >= 6 && /\d[.,]\d/.test(buffer)) return pending;
    }
    throw new Error(lastRaw
      ? `La báscula respondió "${lastRaw.trim()}" pero no se encontró un peso. Revisa la configuración.`
      : "La báscula no respondió. Revisa el cable, la velocidad y el comando para pedir el peso.");
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
    await port.close().catch(() => undefined);
  }
}
