import { describe, expect, it } from "vitest";

import { DEFAULT_SCALE, parseScaleLine, readWeight, type SerialLike } from "../src/input/scale";

describe("weight in what a scale sends", () => {
  it("takes the number, its unit and the motion flag from common formats", () => {
    expect(parseScaleLine("  0.375 kg\r")).toMatchObject({ kilos: 0.375, stable: true });
    expect(parseScaleLine("ST,GS,+0001.250kg")).toMatchObject({ kilos: 1.25, stable: true });
    expect(parseScaleLine("US,GS,+0000.980kg")).toMatchObject({ kilos: 0.98, stable: false });
    expect(parseScaleLine("W: 2,500")).toMatchObject({ kilos: 2.5 });
    expect(parseScaleLine("001.250")).toMatchObject({ kilos: 1.25 });
    expect(parseScaleLine("\x02  375 g\x03")).toMatchObject({ kilos: 0.375 });
    expect(parseScaleLine("1.00 lb")!.kilos).toBe(0.454);
  });

  it("ignores lines without a weight", () => {
    expect(parseScaleLine("")).toBeNull();
    expect(parseScaleLine("\r\n")).toBeNull();
    expect(parseScaleLine("ERROR")).toBeNull();
    expect(parseScaleLine("-0.010 kg")).toBeNull();
  });
});

/** A port that answers `reply` once it is asked with `command` (or right away when command is null). */
function fakePort(reply: string, command: string | null): SerialLike & { sent: string[]; opened: boolean } {
  let push: ((chunk: Uint8Array) => void) | null = null;
  const sent: string[] = [];
  const port = {
    sent,
    opened: false,
    async open() { port.opened = true; },
    async close() { port.opened = false; },
    readable: new ReadableStream<Uint8Array>({
      start(controller) {
        push = (chunk) => controller.enqueue(chunk);
        if (command === null) push(new TextEncoder().encode(reply));
      },
    }),
    writable: new WritableStream<Uint8Array>({
      write(chunk) {
        const text = new TextDecoder().decode(chunk);
        sent.push(text);
        if (text === command) push?.(new TextEncoder().encode(reply));
      },
    }),
  };
  return port;
}

describe("reading the scale", () => {
  it("takes a weight the scale sends on its own", async () => {
    const port = fakePort("ST,GS,+0000.750kg\r\n", null);
    const reading = await readWeight(port, { ...DEFAULT_SCALE, timeoutMs: 1000 });
    expect(reading.kilos).toBe(0.75);
    expect(port.sent).toEqual([]);
    expect(port.opened).toBe(false);
  });

  it("asks with the command when the scale stays quiet", async () => {
    const port = fakePort("  1.125 kg\r\n", "P");
    const reading = await readWeight(port, { ...DEFAULT_SCALE, timeoutMs: 1500 });
    expect(reading.kilos).toBe(1.125);
    expect(port.sent).toEqual(["P"]);
  });

  it("says what the scale sent when it is not a weight", async () => {
    const port = fakePort("HELLO\r\n", null);
    await expect(readWeight(port, { ...DEFAULT_SCALE, requestCommand: "", timeoutMs: 600 }))
      .rejects.toThrow(/HELLO/);
  });
});
