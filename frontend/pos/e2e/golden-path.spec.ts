import { expect, test, type Page, type Route } from "@playwright/test";

import { loadFixture, OdooReader, type Order } from "./support/odoo";
import { Register } from "./support/register";

/**
 * Golden Path of the register PWA against real Odoo (see docs/PRUEBAS_PWA_E2E.md).
 * One test per viewport; each step reads back what Odoo stored before moving on.
 *
 *   link the device → PIN (wrong, then right) → open with $500 → online sale →
 *   lost response retried (no duplicate) → two sales offline + reload + reconnect →
 *   reload online (no resend) → return authorized by the manager's PIN, retried →
 *   closing with the exact count.
 */
const fixture = loadFixture();
const [soda, cookies] = fixture.products as [typeof fixture.products[number], typeof fixture.products[number]];
const OPENING = 500;
const PAID_STATES = ["paid", "done", "invoiced"];

test("Golden Path de la caja PWA contra Odoo real", async ({ page, context, baseURL }, info) => {
  test.setTimeout(420_000);
  const mobile = info.project.name === "mobile";
  const token = fixture.tokens[info.project.name];
  expect(token, `fixture without a token for ${info.project.name}`).toBeTruthy();
  const odoo = new OdooReader(baseURL!, fixture);
  const pos = new Register(page, mobile);
  const sales: string[] = [];

  await test.step("0. Precondición: la caja está cerrada (base desechable nueva o corrida anterior completa)", async () => {
    const session = await odoo.lastSession();
    expect(session === undefined || session.state === "closed",
      `la caja tiene la sesión ${session?.name} en ${session?.state}: prepara una base nueva con pwa_e2e_env.py`).toBe(true);
  });

  await test.step("1. Vincular el equipo con el token de la caja", async () => {
    await pos.bind(token!, fixture.register.name);
    await pos.waitForServiceWorker();
    await expect.poll(() => pos.localProducts(), { message: "the catalog reached this device", timeout: 30_000 })
      .toBeGreaterThanOrEqual(fixture.products.length);
  });

  await test.step("2. PIN incorrecto y luego el correcto (lo revisa el servidor)", async () => {
    const wrong = fixture.cashier.pin.replace(/.$/, (d) => String((Number(d) + 1) % 10));
    await pos.login(fixture.cashier.name, wrong);
    await expect(page.getByRole("alert").filter({ hasText: "PIN incorrecto." })).toBeVisible();
    await pos.typePin(page.locator("body"), fixture.cashier.pin);
    await expect(page.getByRole("heading", { name: `Abrir ${fixture.register.name}` })).toBeVisible();
  });

  await test.step("3. Abrir la caja con $500 → sesión abierta en Odoo", async () => {
    await pos.openRegister(OPENING);
    const session = await odoo.lastSession();
    expect(session?.state).toBe("opened");
    expect(session?.cash_register_balance_start).toBe(OPENING);
  });

  await test.step("4. Venta en línea en efectivo → una orden pagada con sus líneas y su cajera", async () => {
    const before = await pos.outbox();
    await pos.scan(soda.barcode);
    await pos.scan(soda.barcode);
    await pos.scan(cookies.barcode);
    await expect(pos.cart().getByRole("status", { name: "Cantidad" }).first()).toHaveText("2");
    await pos.charge();
    await pos.pay("Efectivo", 100);
    await expect(page.getByText("Cambio")).toBeVisible();
    await pos.finish();
    const [uuid] = await pos.newSales(before);
    sales.push(uuid!);
    await pos.waitSent();

    const [order, ...extra] = await odoo.ordersByUuid([uuid!]);
    expect(extra, "one order per uuid").toHaveLength(0);
    expectPaid(order, 55.5);
    expect(order!.employee_id && order!.employee_id[0]).toBe(fixture.cashier.id);
    expect(order!.vlux_api_employee_verified).toBe(true);
    const lines = await odoo.lines(order!.lines);
    expect(lines.map((l) => [l.product_id && l.product_id[0], l.qty]).sort()).toEqual(
      [[soda.id, 2], [cookies.id, 1]].sort());
    const payments = await odoo.payments(order!.payment_ids);
    const cash = payments.filter((p) => p.payment_method_id && p.payment_method_id[0] === fixture.payment_methods.cash);
    expect(round(cash.reduce((t, p) => t + p.amount, 0))).toBe(55.5);
  });

  await test.step("5. Respuesta perdida: Odoo la registra, la caja reintenta y no se duplica (tarjeta)", async () => {
    const lost = await loseFirstResponse(page, "**/vlux/api/v1/orders");
    const before = await pos.outbox();
    await pos.scan(cookies.barcode);
    await pos.charge();
    await pos.pay("Tarjeta");
    await pos.finish();
    const [uuid] = await pos.newSales(before);
    sales.push(uuid!);
    await expect.poll(() => lost.serverStatus, { message: "Odoo answered the first send" }).toBe(200);
    const rows = await pos.waitSent();
    expect(rows.find((row) => row.uuid === uuid)!.attempts, "sent again after the lost answer").toBeGreaterThanOrEqual(2);
    const orders = await odoo.ordersByUuid([uuid!]);
    expect(orders, "the retry did not create a second order").toHaveLength(1);
    expectPaid(orders[0], cookies.price);
    await lost.stop();
  });

  await test.step("6. Sin internet: venta, recargar la app, PIN en el equipo, otra venta y volver la red → exactamente dos órdenes", async () => {
    await context.setOffline(true);
    await expect(page.getByText("Sin internet").filter({ visible: true }).first()).toBeVisible();
    const sellOffline = async (product: typeof soda) => {
      const before = await pos.outbox();
      await pos.scan(product.barcode);
      await pos.charge();
      await expect(page.getByText("Calculado sin internet")).toBeVisible();
      await pos.pay("Efectivo");
      await pos.finish();
      const [uuid] = await pos.newSales(before);
      return uuid!;
    };
    // With the session the server opened for the cashier (online PIN): the sale is verified.
    const withSession = await sellOffline(soda);

    // A reload without network: the service worker serves the app, the queue survives,
    // and the PIN can only be checked on the device: that sale is marked unverified.
    await page.reload();
    await pos.login(fixture.cashier.name, fixture.cashier.pin);
    await expect(pos.searchBox()).toBeVisible();
    const deviceOnly = await sellOffline(cookies);
    const offline = [withSession, deviceOnly];
    expect((await pos.outbox()).filter((row) => offline.includes(row.uuid) && row.status === "pending")).toHaveLength(2);
    expect(await odoo.ordersByUuid(offline), "nothing reached Odoo while offline").toHaveLength(0);

    await context.setOffline(false);
    await pos.waitSent();
    sales.push(...offline);
    const orders = await odoo.ordersByUuid(offline);
    expect(orders, "each offline sale exactly once").toHaveLength(2);
    const byUuid = new Map(orders.map((o) => [o.uuid, o]));
    expect(byUuid.get(withSession)!.amount_total).toBe(soda.price);
    expect(byUuid.get(deviceOnly)!.amount_total).toBe(cookies.price);
    for (const order of orders) expect(PAID_STATES).toContain(order.state);
    expect(byUuid.get(withSession)!.vlux_api_employee_verified, "server-checked PIN → verified").toBe(true);
    expect(byUuid.get(deviceOnly)!.vlux_api_employee_verified, "PIN checked only on the device → unverified").toBe(false);
  });

  await test.step("7. Recargar con internet no reenvía nada", async () => {
    await page.reload();
    await pos.login(fixture.cashier.name, fixture.cashier.pin);
    await expect(pos.searchBox()).toBeVisible();
    await pos.waitSent();
    const session = await odoo.lastSession();
    const orders = (await odoo.ordersOfSession(session!.id)).filter((o) => !o.is_refund);
    expect(orders.map((o) => o.uuid).sort()).toEqual([...sales].sort());
  });

  await test.step("8. Devolución con el PIN del encargado; el reintento no la duplica", async () => {
    const [sale] = await odoo.ordersByUuid([sales[0]!]);
    const folio = String(sale!.pos_reference);
    await pos.module("Ventas y devoluciones");
    await page.getByRole("searchbox", { name: "Folio del ticket" }).fill(folio);
    await page.getByRole("button", { name: "Buscar" }).click();
    const devolver = page.getByRole("button", { name: "Devolver" });
    if (fixture.refunds_need_manager) await expect(devolver).toHaveAttribute("title", "Pide el PIN de un encargado");
    await devolver.click();

    const dialog = page.getByRole("dialog", { name: "Autorizar: Autorizar devolución" });
    // The owner may authorize too: with more than one, the register asks who.
    const chooser = dialog.getByRole("button", { name: fixture.manager.name });
    if (await chooser.isVisible()) await chooser.click();
    await expect(dialog.getByText(`PIN de ${fixture.manager.name}`)).toBeVisible();
    await pos.typePin(dialog, fixture.manager.pin);
    await expect(page.getByRole("heading", { name: "Devolución" })).toBeVisible();

    await page.getByLabel(`Cantidad que regresa de ${soda.name}`).fill("1");
    await page.getByRole("button", { name: "Calcular devolución" }).click();
    const confirm = page.getByRole("button", { name: /^Devolver (MX)?\$20\.00$/ });
    await expect(confirm).toBeVisible();

    const lost = await loseFirstResponse(page, "**/vlux/api/v1/orders/refund");
    await confirm.click();
    await expect.poll(() => lost.serverStatus, { message: "Odoo recorded the first try" }).toBe(200);
    await expect(page.getByRole("alert").first()).toBeVisible(); // the cashier sees the connection error
    await confirm.click(); // the cashier taps again: same return, same uuid
    await expect(page.getByText("Devolución registrada")).toBeVisible();
    await lost.stop();

    const session = await odoo.lastSession();
    const refunds = (await odoo.ordersOfSession(session!.id)).filter((o) => o.is_refund);
    expect(refunds, "one return, not two").toHaveLength(1);
    expect(refunds[0]!.amount_total).toBe(-soda.price);
    const [original] = await odoo.ordersByUuid([sales[0]!]);
    const lines = await odoo.lines(original!.lines);
    expect(lines.find((l) => l.product_id && l.product_id[0] === soda.id)!.refunded_qty).toBe(1);
    await page.getByRole("button", { name: /Listo|Terminar|Nueva venta/ }).first().click();
  });

  await test.step("9. Corte de caja con el conteo exacto → sesión cerrada sin diferencia", async () => {
    // Cash: opening + 55.50 + 20.00 + 15.50 − 20.00 returned; card: 15.50.
    const expectedCash = round(OPENING + 55.5 + soda.price + cookies.price - soda.price);
    await pos.module("Corte de caja");
    await page.getByLabel("Efectivo contado en el cajón").fill(String(expectedCash));
    await page.getByLabel(/^Tarjeta según terminal/).fill(String(cookies.price));
    await page.getByRole("button", { name: "Cerrar caja" }).click();
    await expect(page.getByRole("heading", { name: "Caja cerrada" })).toBeVisible();

    const session = await odoo.lastSession();
    expect(session?.state).toBe("closed");
    expect(session?.cash_register_balance_end_real).toBe(expectedCash);
    expect(session?.cash_register_difference).toBe(0);
    const orders = await odoo.ordersOfSession(session!.id);
    expect(orders).toHaveLength(sales.length + 1);
    expect(new Set(orders.map((o) => o.uuid)).size, "no uuid twice").toBe(orders.length);
    await page.getByRole("button", { name: "Terminar" }).click();
  });
});

function expectPaid(order: Order | undefined, total: number) {
  expect(order, "the order exists in Odoo").toBeTruthy();
  expect(PAID_STATES).toContain(order!.state);
  expect(order!.amount_total).toBe(total);
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * The first POST to `pattern` really reaches Odoo, but its answer never gets
 * back to the app (the connection drops): exactly a lost response.
 */
async function loseFirstResponse(page: Page, pattern: string) {
  const state = { hits: 0, serverStatus: 0, stop: async () => { await page.unroute(pattern, handler); } };
  async function handler(route: Route) {
    if (route.request().method() !== "POST" || state.hits > 0) return route.continue();
    state.hits += 1;
    const response = await route.fetch();
    state.serverStatus = response.status();
    await route.abort("connectionreset");
  }
  await page.route(pattern, handler);
  return state;
}
