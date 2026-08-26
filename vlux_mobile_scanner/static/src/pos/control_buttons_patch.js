import { patch } from "@web/core/utils/patch";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { VluxMobilePairingDialog } from "./pairing_dialog";

patch(ControlButtons.prototype, {
    async openVluxMobileScannerPairing() {
        try {
            const pairing = await this.pos.vluxCreateMobilePairing();
            this.dialog.add(VluxMobilePairingDialog, { pairing, pos: this.pos });
        } catch (error) {
            this.notification.add(
                error?.message || "No fue posible crear el codigo de conexion.",
                { title: "VLUX Mobile Scanner", type: "danger" }
            );
        }
    },
});
