import { describe, expect, it } from "vitest";

import { PhoneScannerSource } from "../src/input/phoneScanner";
import type { ScanOutcome } from "../src/input/sources";
import { fakeClient, ok } from "./helpers";

const until = async (check: () => boolean) => {
  for (let i = 0; i < 100 && !check(); i++) await new Promise((r) => setTimeout(r, 20));
};

describe("phone scanner source", () => {
  it("hands each read to the sale once, answers the phone, and stops when unlinked", async () => {
    let round = 0;
    const { client, calls } = fakeClient((method, path) => {
      if (path.startsWith("/registers/1/scanner/events")) {
        round += 1;
        if (round === 1) return ok({ status: "paired", items: [{ request_id: "r1", barcode: "750" }, { request_id: "r2", barcode: "999" }] });
        if (round === 2) return ok({ status: "paired", items: [{ request_id: "r1", barcode: "750" }] }); // answer lost: same read again
        return ok({ status: "revoked", items: [] });
      }
      return ok({ status: "delivered" });
    });
    const seen: string[] = [];
    let ended = false;
    const handler = async (code: string): Promise<ScanOutcome> => {
      seen.push(code);
      return code === "750"
        ? { status: "delivered", code: "ADDED_TO_CART", message: "Agregado", product: { id: 5, name: "Refresco", price: 18 } }
        : { status: "not_found", code: "PRODUCT_NOT_FOUND", message: "No está" };
    };
    const stop = new PhoneScannerSource(client, 1, { pairingId: 7, deviceId: "dev-1234" }, () => { ended = true; }).start(handler);
    await until(() => ended);
    stop();
    expect(seen).toEqual(["750", "999"]);
    const acks = calls.filter((call) => call.path === "/registers/1/scanner/ack").map((call) => call.body);
    expect(acks).toEqual([
      { device_id: "dev-1234", request_id: "r1", status: "delivered", result_code: "ADDED_TO_CART", message: "Agregado", product_id: 5, product_name: "Refresco", unit_price: 18 },
      { device_id: "dev-1234", request_id: "r2", status: "not_found", result_code: "PRODUCT_NOT_FOUND", message: "No está" },
      { device_id: "dev-1234", request_id: "r1", status: "delivered", result_code: "ADDED_TO_CART", message: "Agregado" },
    ]);
    expect(calls[0]!.path).toBe("/registers/1/scanner/events?pairing_id=7&device_id=dev-1234");
  });
});
