import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";

// Cents: a balance equal to the limit is still within it.
const TOLERANCE = 0.005;

patch(OrderPaymentValidation.prototype, {
    async isOrderValid(isForceValidate) {
        if (!(await super.isOrderValid(...arguments))) {
            return false;
        }
        const order = this.order;
        if (!order.vluxIsCreditSale) {
            return true;
        }
        const refuse = (body) => {
            this.pos.dialog.add(AlertDialog, { title: _t("Venta a crédito"), body });
            return false;
        };
        if (!this.pos.vluxCanSellOnCredit) {
            return refuse(_t("Sólo el encargado o el dueño pueden vender a crédito."));
        }
        const partner = order.getPartner();
        if (!partner) {
            // Odoo already asks for the customer before this point.
            return refuse(_t("Elige al cliente al que se le fía."));
        }
        const status = await this.pos.vluxCreditStatus(partner);
        if (!status.allowed) {
            return refuse(
                _t("%s no tiene crédito autorizado. El encargado puede autorizarlo.", partner.name)
            );
        }
        const amount = order.vluxCreditAmount;
        if (status.limit && status.balance + amount > status.limit + TOLERANCE) {
            const money = (value) => this.pos.env.utils.formatCurrency(value);
            return refuse(
                _t(
                    "%(name)s debe %(balance)s y su límite es %(limit)s: esta venta de %(amount)s lo rebasa. Puede pagar una parte en efectivo.",
                    {
                        name: partner.name,
                        balance: money(status.balance),
                        limit: money(status.limit),
                        amount: money(amount),
                    }
                )
            );
        }
        // Printed on the ticket (saldo anterior / saldo nuevo) and kept with
        // the order so a reprint shows the same figures.
        order.vlux_credit_prev_balance = status.balance;
        return true;
    },

    async afterOrderValidation() {
        // Keep the customer's balance on this register current (customer
        // list, next sale) without reloading; the server holds the truth.
        const order = this.order;
        const partner = order.getPartner();
        const amount = order.vluxCreditAmount;
        if (partner && amount) {
            const before = order.vluxIsCreditSale
                ? order.vlux_credit_prev_balance || 0
                : partner.vlux_credit_balance || 0;
            partner.vlux_credit_balance = before + amount;
        }
        return await super.afterOrderValidation(...arguments);
    },
});
