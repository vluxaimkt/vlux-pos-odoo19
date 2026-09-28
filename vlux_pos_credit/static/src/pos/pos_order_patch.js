import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

patch(PosOrder.prototype, {
    /** Part of the ticket paid with "Crédito" (Odoo's customer account). */
    get vluxCreditAmount() {
        return this.payment_ids
            .filter((payment) => payment.payment_method_id?.type === "pay_later")
            .reduce((total, payment) => total + payment.getAmount(), 0);
    },

    /** A sale (not a refund or an abono) that leaves the customer owing money. */
    get vluxIsCreditSale() {
        return !this.isRefund && this.vluxCreditAmount > 0;
    },

    /** Balance after this ticket, from the balance captured at validation. */
    get vluxCreditNewBalance() {
        return (this.vlux_credit_prev_balance || 0) + this.vluxCreditAmount;
    },
});
