import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        // Only the encargado and the owner sell on credit (decision D7): a
        // cashier does not even see the option. The server flags any sale on
        // credit made by someone else anyway.
        if (!this.pos.vluxCanSellOnCredit) {
            this.payment_methods_from_config = this.payment_methods_from_config.filter(
                (method) => method.type !== "pay_later"
            );
        }
    },
});
