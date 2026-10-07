import { useEffect, useState } from "preact/hooks";

import type { OutboxRow } from "../db/db";
import { formatDateTime } from "../lib/locale";
import { formatMoney } from "../lib/money";
import { usePos } from "../state";
import { Icon } from "../ui/Icon";
import { Banner, EmptyState, PageHeader, Section } from "../ui/Page";
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

  const open = rows.filter((row) => row.status !== "sent");
  const sent = rows.filter((row) => row.status === "sent");
  const item = (row: OutboxRow) => (
    <div key={row.uuid} class="row !items-start !py-3">
      <span class="method-icon !w-9 !h-9 !rounded-[10px] shrink-0" aria-hidden="true" style={{ background: STATUS_COLOR[row.status] }}>
        <Icon name={row.status === "sent" ? "check" : row.status === "attention" ? "warning" : "tray"} size={18} />
      </span>
      <div class="flex-1 min-w-0">
        <div class="flex gap-2 items-center">
          <span class="font-semibold">{STATUS_LABEL[row.status]}</span>
          <span class="font-mono text-xs label-2 truncate">{row.result?.pos_reference ?? row.uuid.slice(0, 8)}</span>
        </div>
        <div class="text-xs label-2">{formatDateTime(row.createdAt)}</div>
        {row.lastError && row.status !== "sent" && <div class="text-sm text-danger mt-1">{row.lastError.message}</div>}
      </div>
      <span class="font-semibold num">{formatMoney(row.body.expected_total ?? 0, setup.store.currency)}</span>
      {row.status === "attention" && (
        <button class="btn btn-sm" disabled={busy || !online} onClick={() => void retry(row.uuid)}>
          <Icon name="refresh" size={16} /> Reintentar
        </button>
      )}
    </div>
  );

  return (
    <section class="p-4 lg:p-8 max-w-3xl mx-auto flex flex-col gap-6 rise">
      <PageHeader title="Ventas por enviar" icon="tray" back={onClose}
        actions={
          <button class="btn btn-primary btn-sm" disabled={busy || !online} onClick={() => void sendNow()}>
            {busy ? <span class="loading loading-spinner loading-sm" /> : <><Icon name="arrowUp" size={16} /> Enviar ahora</>}
          </button>
        } />
      {!online && <Banner tone="warn">Sin internet: se enviarán solas cuando vuelva.</Banner>}
      {open.length
        ? <Section title={`Pendientes (${open.length})`}>{open.map(item)}</Section>
        : <EmptyState icon="checkCircle" title="No hay ventas pendientes" hint="Todo lo cobrado en esta caja ya está en el servidor." />}
      {sent.length > 0 && <Section title="Enviadas recientemente">{sent.map(item)}</Section>}
    </section>
  );
}

const STATUS_COLOR: Record<OutboxRow["status"], string> = {
  pending: "linear-gradient(180deg, #ffb340, #ff9500)",
  attention: "linear-gradient(180deg, #ff6961, #d70015)",
  sent: "linear-gradient(180deg, #34c759, #248a3d)",
};
