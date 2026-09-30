import type { Employee } from "../api/types";

export async function sha1Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/**
 * Check a PIN without the network, as the Odoo POS does with the same hashes
 * (GET /registers/<id>/employees). An employee without a PIN needs none.
 */
export async function pinMatches(employee: Employee, pin: string): Promise<boolean> {
  if (!employee.pin_sha1) return true;
  return (await sha1Hex(pin)) === employee.pin_sha1;
}
