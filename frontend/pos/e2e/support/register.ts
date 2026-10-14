import { expect, type Locator, type Page } from "@playwright/test";

/** A row of the register's local queue (IndexedDB "vlux-pos", table "outbox"). */
export interface OutboxRow {
  uuid: string;
  status: "pending" | "sent" | "attention";
  attempts: number;
  lastError?: string;
}

/**
 * The register as a person uses it: the visible labels of the PWA, never its
 * internals. The only exception is reading the local sales queue (uuids and
 * their state), which is what proves a sale was sent once.
 */
export class Register {
  constructor(readonly page: Page, readonly mobile: boolean) {}

  /** Link this browser to the register with its token and wait for the employee list. */
  async bind(token: string, registerName: string): Promise<void> {
    await this.page.goto("/vlux-pos/");
    await this.page.getByPlaceholder("Token de la caja").fill(token);
    await this.page.getByRole("button", { name: "Conectar" }).click();
    await this.page.getByRole("button", { name: `Usar este equipo como ${registerName}` }).click();
    await expect(this.page.getByRole("heading", { name: "¿Quién está en la caja?" })).toBeVisible();
  }

  /**
   * The service worker caches the app shell; without clients.claim() the first
   * page is not controlled yet, so reload once until it is (needed offline).
   */
  async waitForServiceWorker(): Promise<void> {
    const active = () => this.page.evaluate(async () => !!(await navigator.serviceWorker.getRegistration())?.active);
    // The app registers the worker on every load; if fetching sw.js failed
    // (a slow server), only a new load registers it again.
    for (let attempt = 0; attempt < 3 && !(await poll(active, 30_000)); attempt += 1) await this.page.reload();
    expect(await active(), "the service worker is installed (needed to work offline)").toBe(true);
    if (!(await this.page.evaluate(() => !!navigator.serviceWorker.controller))) await this.page.reload();
    await expect.poll(() => this.page.evaluate(() => !!navigator.serviceWorker.controller),
      { message: "the service worker controls the page" }).toBe(true);
  }

  async login(name: string, pin: string): Promise<void> {
    await this.page.getByRole("button", { name }).click();
    await this.typePin(this.page.locator("body"), pin);
  }

  /** Tap the digits on the round keypad inside `scope`. */
  async typePin(scope: Locator, pin: string): Promise<void> {
    for (const digit of pin) await scope.getByRole("button", { name: digit, exact: true }).click();
  }

  async openRegister(cash: number): Promise<void> {
    await this.page.getByLabel("Efectivo inicial en el cajón").fill(String(cash));
    await this.page.getByRole("button", { name: "Abrir caja" }).click();
    await expect(this.searchBox()).toBeVisible();
  }

  searchBox(): Locator {
    return this.page.getByRole("searchbox", { name: "Escanea o busca un producto" });
  }

  /**
   * A keyboard-wedge scanner: types the code and presses Enter, then waits
   * until the register took it (it clears the search box), as a person scanning
   * the next item would. Codes fired in the same millisecond can race the
   * previous one's clearing: that is checked with a real scanner (H06).
   */
  async scan(barcode: string): Promise<void> {
    await this.searchBox().fill(barcode);
    await this.searchBox().press("Enter");
    await expect(this.searchBox()).toHaveValue("");
  }

  cart(): Locator {
    return this.page.getByRole("complementary", { name: "Venta" });
  }

  async charge(): Promise<void> {
    await this.page.getByRole("button", { name: "Cobrar" }).filter({ visible: true }).first().click();
    await expect(this.page.getByRole("heading", { name: "Cobrar" })).toBeVisible();
  }

  /** Pay with a method; `amount` empty pays what is left. */
  async pay(method: "Efectivo" | "Tarjeta", amount?: number): Promise<void> {
    if (amount !== undefined) await this.page.getByRole("spinbutton").fill(String(amount));
    await this.page.getByRole("button", { name: new RegExp(`^${method}`) }).click();
  }

  /** "Terminar venta" → the ticket; returns to an empty sale. */
  async finish(): Promise<void> {
    await this.page.getByRole("button", { name: "Terminar venta" }).click();
    await expect(this.page.getByRole("heading", { name: /Venta registrada/ })).toBeVisible();
    await this.page.getByRole("button", { name: "Nueva venta" }).click();
    await expect(this.searchBox()).toBeVisible();
  }

  /** Go to a module from the sidebar (a drawer behind "Abrir menú" on phones). */
  async module(label: string): Promise<void> {
    if (this.mobile) await this.page.getByRole("button", { name: "Abrir menú" }).click();
    await this.page.getByRole("navigation").getByRole("button", { name: label, exact: true }).click();
  }

  /** The local queue of sales, as stored by the app. */
  outbox(): Promise<OutboxRow[]> {
    return this.page.evaluate(() => new Promise<OutboxRow[]>((resolve, reject) => {
      const open = indexedDB.open("vlux-pos");
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const db = open.result;
        const read = db.transaction("outbox", "readonly").objectStore("outbox").getAll();
        read.onsuccess = () => {
          resolve((read.result as OutboxRow[]).map(({ uuid, status, attempts, lastError }) => ({ uuid, status, attempts, lastError })));
          db.close();
        };
        read.onerror = () => reject(read.error);
      };
    }));
  }

  /** Products downloaded to this device (IndexedDB "products"). */
  localProducts(): Promise<number> {
    return this.page.evaluate(() => new Promise<number>((resolve, reject) => {
      const open = indexedDB.open("vlux-pos");
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const db = open.result;
        const count = db.transaction("products", "readonly").objectStore("products").count();
        count.onsuccess = () => { resolve(count.result); db.close(); };
        count.onerror = () => reject(count.error);
      };
    }));
  }

  /** Wait until no sale is waiting to be sent (none may need attention). */
  async waitSent(timeout = 90_000): Promise<OutboxRow[]> {
    await expect.poll(async () => (await this.outbox()).filter((row) => row.status !== "sent").length,
      { timeout, intervals: [500, 1000, 2000] }).toBe(0);
    return this.outbox();
  }

  /** The uuids queued after `before` (the sales just made). */
  async newSales(before: OutboxRow[]): Promise<string[]> {
    const known = new Set(before.map((row) => row.uuid));
    return (await this.outbox()).filter((row) => !known.has(row.uuid)).map((row) => row.uuid);
  }
}

/** True as soon as `check` is, false after `timeout` ms. */
async function poll(check: () => Promise<boolean>, timeout: number): Promise<boolean> {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await check()) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}
