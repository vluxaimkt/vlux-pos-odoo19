import { ApiError, type ApiClient } from "../api/client";
import type { ScanHandler, ScanSource } from "./sources";

export interface PhonePairing {
  pairingId: number;
  deviceId: string;
}

const POLL_MS = 1000;
const IDLE_POLL_MS = 3000;

/**
 * A phone linked by QR as this register's scanner (vlux_mobile_scanner).
 *
 * The register holds no Odoo session, so it asks the server for the reads
 * waiting for it (once a second, only while linked and visible) and answers
 * each one, which the phone shows. Reads are handled one at a time, in order.
 */
export class PhoneScannerSource implements ScanSource {
  constructor(
    private readonly client: ApiClient,
    private readonly registerId: number,
    private readonly pairing: PhonePairing,
    /** Told when the server says the link ended (phone unlinked, register closed, expired). */
    private readonly onEnded: () => void,
  ) {}

  start(handler: ScanHandler): () => void {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const handled = new Set<string>();
    const tick = async () => {
      if (stopped) return;
      let delay = POLL_MS;
      try {
        if (typeof document !== "undefined" && document.hidden) {
          delay = IDLE_POLL_MS;
        } else {
          const { status, items } = await this.client.scannerEvents(this.registerId, this.pairing);
          if (status !== "paired" && status !== "waiting") {
            stopped = true;
            this.onEnded();
            return;
          }
          if (status === "waiting") delay = IDLE_POLL_MS;
          for (const event of items) {
            if (stopped) return;
            // Answered already but the answer did not arrive: answer again, do not add twice.
            const outcome = handled.has(event.request_id)
              ? { status: "delivered" as const, code: "ADDED_TO_CART", message: "Agregado" }
              : await handler(event.barcode);
            handled.add(event.request_id);
            await this.client.scannerAck(this.registerId, {
              device_id: this.pairing.deviceId,
              request_id: event.request_id,
              status: outcome.status,
              result_code: outcome.code,
              message: outcome.message,
              ...(outcome.product
                ? { product_id: outcome.product.id, product_name: outcome.product.name, unit_price: outcome.product.price }
                : {}),
            });
          }
        }
      } catch (error) {
        // The link is gone (another register, unpaired, revoked token): stop for good.
        if (error instanceof ApiError && ["NOT_FOUND", "FORBIDDEN", "INVALID_TOKEN"].includes(error.code)) {
          stopped = true;
          this.onEnded();
          return;
        }
        // Offline or a hiccup: try again a bit later; the phone keeps its read pending.
        delay = IDLE_POLL_MS;
      }
      if (!stopped) timer = setTimeout(() => void tick(), delay);
    };
    void tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }
}
