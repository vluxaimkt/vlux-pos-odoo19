import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

patch(PosStore.prototype, {
    /** Who may sell on credit is the register's option; by default the encargado or the owner. */
    get vluxCanSellOnCredit() {
        return (
            this.config.vlux_credit_sellers === "all" ||
            !this.config.module_pos_hr ||
            Boolean(this.employeeIsAdmin)
        );
    },

    /**
     * The customer's credit as the server knows it now; offline, what the
     * register loaded (the server re-checks every sale when it syncs).
     */
    async vluxCreditStatus(partner) {
        try {
            return await this.data.call("res.partner", "vlux_pos_credit_status", [partner.id]);
        } catch {
            return {
                allowed: Boolean(partner.vlux_credit_allowed),
                limit: partner.vlux_credit_limit || 0,
                balance: partner.vlux_credit_balance || 0,
                offline: true,
            };
        }
    },
});
