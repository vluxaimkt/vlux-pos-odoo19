import { patch } from "@web/core/utils/patch";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { VluxMobilePairingDialog } from "./pairing_dialog";

patch(ControlButtons.prototype, {
    /**
     * The owner or the encargado opened the POS on their own phone: the phone
     * itself becomes the scanner (no QR to read), and what it scans lands on
     * the register's screen.
     */
    get vluxPhoneIsTheScanner() {
        const manager = !this.pos.config.module_pos_hr || this.pos.employeeIsAdmin;
        return Boolean(this.ui.isSmall && manager);
    },

    async openVluxMobileScannerPairing() {
        try {
            if (this.vluxPhoneIsTheScanner) {
                const pairing = await this.pos.vluxCreateMobilePairing({ anyRegister: true });
                window.location.assign(pairing.scanner_url);
                return;
            }
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
