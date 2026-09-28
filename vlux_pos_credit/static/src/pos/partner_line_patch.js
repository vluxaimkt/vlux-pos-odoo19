import { patch } from "@web/core/utils/patch";
import { PartnerLine } from "@point_of_sale/app/screens/partner_list/partner_line/partner_line";
import { VluxCreditDialog } from "./credit_dialog";
import { VluxAbonoDialog } from "./abono_dialog";

patch(PartnerLine.prototype, {
    vluxOpenCredit(partner) {
        this.pos.dialog.add(VluxCreditDialog, { partner });
    },
    vluxOpenAbono(partner) {
        this.pos.dialog.add(VluxAbonoDialog, { partner });
    },
});
