import { useEffect, useState } from "preact/hooks";

import type { OutboxRow } from "../db/db";
import { formatDateTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { usePos } from "../state";
import { retrySale } from "../sync/outbox";

const STATUS_LABEL: Record<OutboxRow["status"], string> = {
  pending: "Por enviar",
  attention: "Por revisar",
  sent: "Enviada",
};

/**
 * Sales that have not reached the server, or that it refused. Nothing here is
 * ever deleted by the app: a refused sale was still paid for, and the owner
 * decides what happens to it.
 */
export function QueueScreen({ onClose }: { onClose: () => void }) {
  const { db, setup, online, flushNow } = usePos();
  const [rows, setRows] = useState<OutboxRow[]>([]);
  const [busy, setBusy] = useState(false);

  async function load() {
    const open = await db.outbox.where("status").anyOf("pending", "attention").sortBy("createdAt");
    const recent = (await db.outbox.where("status").equals("sent").reverse().sortBy("createdAt")).slice(0, 10);
    setRows([...open, ...recent]);
  }

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 3000);
    return () => clearInterval(id);
  }, [db]);

  async function sendNow() {
    setBusy(true);
    try {
      await flushNow();
    } finally {
      await load();
      setBusy(false);
    }
  }

  async function retry(uuid: string) {
    await retrySale(db, uuid);
    await sendNow();
  }

  return (
    <section class="p-4 flex flex-col gap-3 max-w-3xl mx-auto">
      <div class="flex items-center gap-2">
        <h2 class="text-xl flex-1">Ventas por enviar</h2>
        <button class="btn btn-primary btn-sm" disabled={busy || !online} onClick={() => void sendNow()}>
          {busy ? <span class="loading loading-spinner loading-sm" /> : "Enviar ahora"}
        </button>
        <button class="btn btn-ghost btn-sm" onClick={onClose}>Volver a vender</button>
      </div>
      {!online && <div role="alert" class="alert alert-warning">Sin internet: se enviarán solas cuando vuelva.</div>}
      {!rows.length && <p class="opacity-60">No hay ventas pendientes.</p>}
      <ul class="list bg-base-100 rounded-box">
        {rows.map((row) => (
          <li key={row.uuid} class="list-row items-start">
            <div class="list-col-grow">
              <div class="flex gap-2 items-center">
                <span class={`badge ${row.status === "attention" ? "badge-error" : row.status === "pending" ? "badge-warning" : "badge-success"}`}>
                  {STATUS_LABEL[row.status]}
                </span>
                <span class="font-mono text-xs opacity-60">{row.result?.pos_reference ?? row.uuid.slice(0, 8)}</span>
              </div>
              <div class="text-sm">{formatDateTime(row.createdAt)}</div>
              {row.lastError && row.status !== "sent" && <div class="text-sm text-error">{row.lastError.message}</div>}
            </div>
            <div class="font-semibold">{formatMoney(row.body.expected_total ?? 0, setup.store.currency)}</div>
            {row.status === "attention" && (
              <button class="btn btn-sm" disabled={busy || !online} onClick={() => void retry(row.uuid)}>Reintentar</button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
