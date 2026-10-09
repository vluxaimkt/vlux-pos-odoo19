import type { Employee, RegisterOptions } from "../api/types";

/**
 * Whether a return needs a manager's PIN: the store's option (on by
 * default on the server), registers with employee login, and someone at the
 * register who is not a manager. The server checks it again.
 */
export function refundNeedsManager(employeeLogin: boolean, person: Pick<Employee, "role"> | null, options?: RegisterOptions): boolean {
  if (!employeeLogin || !person) return false;
  if (options?.refunds_need_manager === false) return false;
  return person.role !== "manager";
}
