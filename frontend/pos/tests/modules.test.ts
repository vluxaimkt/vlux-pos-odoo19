import { describe, expect, it } from "vitest";

import type { Employee } from "../src/api/types";
import { type AccessContext, authorizersFor, canOpen, isAvailable, moduleById, MODULES } from "../src/modules";

const person = (id: number, role: Employee["role"], extra: Partial<Employee> = {}): Employee => ({
  id, name: `E${id}`, role, user_id: null, pin_sha1: "x", barcode_sha1: null, ...extra,
});
const cashier = person(1, "cashier");
const manager = person(2, "manager", { can_edit_catalog: true });
const owner = person(3, "manager", { is_owner: true, can_manage_staff: true });
const noPin = person(4, "manager", { pin_sha1: null });
const ctx: AccessContext = { employeeLogin: true, sessionOpen: true, options: { cashier_cash_out: false } };

describe("register modules", () => {
  it("every module has its own shortcut", () => {
    expect(new Set(MODULES.map((m) => m.key)).size).toBe(MODULES.length);
  });

  it("locks what the person at the register may not use, as the store's options say", () => {
    expect(canOpen(moduleById("sell"), cashier, ctx)).toBe(true);
    expect(canOpen(moduleById("cash"), cashier, ctx)).toBe(false);
    expect(canOpen(moduleById("cash"), cashier, { ...ctx, options: { cashier_cash_out: true } })).toBe(true);
    expect(canOpen(moduleById("cash"), manager, ctx)).toBe(true);
    expect(canOpen(moduleById("staff"), manager, ctx)).toBe(false);
    expect(canOpen(moduleById("owner"), manager, ctx)).toBe(false);
    expect(canOpen(moduleById("owner"), owner, ctx)).toBe(true);
  });

  it("asks only people who may, have a PIN and are not the one asking", () => {
    const all = [cashier, manager, owner, noPin];
    expect(authorizersFor(moduleById("cash"), all, ctx, cashier).map((e) => e.id)).toEqual([2, 3]);
    expect(authorizersFor(moduleById("owner"), all, ctx, cashier).map((e) => e.id)).toEqual([3]);
    expect(authorizersFor(moduleById("cash"), all, ctx, manager).map((e) => e.id)).toEqual([3]);
  });

  it("without employee login there are no employee or owner modules", () => {
    const plain = { ...ctx, employeeLogin: false };
    expect(isAvailable(moduleById("owner"), plain)).toBe(false);
    expect(isAvailable(moduleById("staff"), plain)).toBe(false);
    expect(canOpen(moduleById("cash"), null, plain)).toBe(true);
  });
});
