import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { formatDateTime, deserializeDateTime } from "@web/core/l10n/dates";

/** The abono ticket: what was paid, the balance before and after. */
export class VluxAbonoReceipt extends Component {
    static template = "vlux_pos_credit.AbonoReceipt";
    static props = { ticket: Object };

    setup() {
        this.pos = usePos();
    }

    get company() {
        return this.pos.company;
    }

    get date() {
        return formatDateTime(deserializeDateTime(this.props.ticket.date));
    }

    formatCurrency(value) {
        return this.env.utils.formatCurrency(value);
    }
}
